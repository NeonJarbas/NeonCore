# # NEON AI (TM) SOFTWARE, Software Development Kit & Application Development System
# # All trademark and other rights reserved by their respective owners
# # Copyright 2008-2021 Neongecko.com Inc.
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import time
import wave

from neon_core.configuration import Configuration
from neon_core.language import get_lang_config
from neon_core.processing_modules.text import TextParsersService

from copy import copy
from mycroft_bus_client import Message
from neon_utils.message_utils import get_message_user
from neon_utils.metrics_utils import Stopwatch
from neon_utils.log_utils import LOG
from neon_utils.configuration_utils import get_neon_device_type
from ovos_utils.json_helper import merge_dict

from mycroft.util.lang import set_default_lang
from mycroft.util.parse import normalize
from mycroft.skills.intent_service import IntentService


try:
    if get_neon_device_type() == "server":
        from neon_transcripts_controller.transcript_db_manager import TranscriptDBManager as Transcribe
    else:
        from neon_transcripts_controller.transcript_file_manager import TranscriptFileManager as Transcribe
except ImportError:
    Transcribe = None


class NeonIntentService(IntentService):
    def __init__(self, bus):
        super().__init__(bus)
        self.config = Configuration.get().get('context', {})
        self.language_config = get_lang_config()

        set_default_lang(self.language_config["internal"])

        self._setup_converse_handlers()

        self.parser_service = TextParsersService(self.bus)
        self.parser_service.start()

        if Transcribe:
            self.transcript_service = Transcribe()
        else:
            self.transcript_service = None

    def _setup_converse_handlers(self):
        self.bus.on('skill.converse.error', self.handle_converse_error)
        self.bus.on('skill.converse.activate_skill',
                    self.handle_activate_skill)
        self.bus.on('skill.converse.deactivate_skill',
                    self.handle_deactivate_skill)
        # backwards compat
        self.bus.on('active_skill_request',
                    self.handle_activate_skill)

    def handle_activate_skill(self, message):
        self.add_active_skill(message.data['skill_id'])

    def handle_deactivate_skill(self, message):
        self.remove_active_skill(message.data['skill_id'])

    def reset_converse(self, message):
        """Let skills know there was a problem with speech recognition"""
        lang = message.data.get('lang', "en-us")
        set_default_lang(lang)
        for skill in copy(self.active_skills):
            self.do_converse([], skill[0], lang, message)

    def handle_utterance(self, message):
        """Main entrypoint for handling user utterances with Mycroft skills

        Monitor the messagebus for 'recognizer_loop:utterance', typically
        generated by a spoken interaction but potentially also from a CLI
        or other method of injecting a 'user utterance' into the system.

        Utterances then work through this sequence to be handled:
        1) Active skills attempt to handle using converse()
        2) Padatious high match intents (conf > 0.95)
        3) Adapt intent handlers
        5) High Priority Fallbacks
        6) Padatious near match intents (conf > 0.8)
        7) General Fallbacks
        8) Padatious loose match intents (conf > 0.5)
        9) Catch all fallbacks including Unknown intent handler

        If all these fail the complete_intent_failure message will be sent
        and a generic info of the failure will be spoken.

        Arguments:
            message (Message): The messagebus data
        """
        self.bus.emit(message.response())  # Notify emitting module that skills is handling this utterance
        try:
            # Get language of the utterance
            lang = message.data.get('lang', self.language_config["user"])
            utterances = message.data.get('utterances', [])

            message.context = message.context or {}
            # Add or init timing data
            if not message.context.get("timing"):
                LOG.warning("No timing data available at intent service")
                message.context["timing"] = {}
            # TODO: This isn't necessarily a transcribe time, should be refactored here and in neon-test-utils DM
            message.context["timing"]["transcribed"] = message.context["timing"].get("transcribed", time.time())
            stopwatch = Stopwatch()

            # Write out text and audio transcripts if service is available
            if self.transcript_service:
                audio = message.context.get("raw_audio")  # This is a tempfile
                if audio:
                    audio = wave.open(audio, 'r')
                    audio = audio.readframes(audio.getnframes())
                audio_file = self.transcript_service.write_transcript(get_message_user(message),
                                                                      message.data.get('utterances', [''])[0],
                                                                      message.context["timing"]["transcribed"],
                                                                      audio)
                message.context["audio_file"] = audio_file

            # pipe utterance trough parsers to get extra metadata
            # use cases: translation, emotion_data, keyword spotting etc.
            # parsers are ordered by priority
            # keep in mind utterance might be modified by previous parser
            with stopwatch:
                for parser in self.parser_service.modules:
                    # mutate utterances and retrieve extra data
                    utterances, data = self.parser_service.parse(parser, utterances, lang)
                    # update message context with extra data
                    message.context = merge_dict(message.context, data)
            message.context["timing"]["text_parsers"] = stopwatch.time
            # normalize() changes "it's a boy" to "it is a boy", etc.
            norm_utterances = [normalize(u.lower(), remove_articles=False)
                               for u in utterances]

            # Build list with raw utterance(s) first, then optionally a
            # normalized version following.
            combined = utterances + list(set(norm_utterances) -
                                         set(utterances))
            # filter empty utterances
            combined = [u for u in combined if u.strip()]
            if len(combined) == 0:
                # STT filters those, but some parser module might do it to
                # abort intent execution
                LOG.debug("Received empty utterance!!")
                reply = message.reply('intent_aborted',
                                      {'utterances': message.data.get('utterances', []),
                                       'lang': lang})
                self.bus.emit(reply)
                return

            # now pass our modified message to mycroft-lib
            message.data["lang"] = lang
            message.data["utterances"] = utterances
            # TODO: Consider how to implement 'and' parsing and converse here DM
            super().handle_utterance(message)
        except Exception as err:
            LOG.exception(err)
