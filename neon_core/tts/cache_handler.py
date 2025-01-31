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
"""
Cache handler - reads all the .dialog files (The default
mycroft responses) and does a tts inference.
It then saves the .wav files to mark1 device

"""

import base64
import glob
import os
import re
import shutil
import hashlib
import json
from urllib import parse
from requests_futures.sessions import FuturesSession
from mycroft.util.log import LOG
from mycroft.util import get_cache_directory


REGEX_SPL_CHARS = re.compile(r'[@#$%^*()<>/\|}{~:]')
MIMIC2_URL = 'https://mimic-api.mycroft.ai/synthesize?text='

# For now we only get the cache for mimic2-kusal
TTS = 'Mimic2'

# Check for more default dialogs
res_path = os.path.abspath(os.path.join(os.path.abspath(__file__), '..',
                                        '..', 'res', 'text', 'en-us'))
wifi_setup_path = '/usr/local/mycroft/mycroft-wifi-setup/dialog/en-us'
cache_dialog_path = [res_path, wifi_setup_path]


def generate_cache_text(cache_audio_dir, cache_text_file):
    """
    This prepares a text file with all the sentences
    from *.dialog files present in
    mycroft/res/text/en-us and mycroft-wifi setup skill
    Args:
        cache_audio_dir (path): path to store .wav files
        cache_text_file (file): file containing the sentences
    """
    try:
        if not os.path.isfile(cache_text_file):
            os.makedirs(cache_audio_dir)
            f = open(cache_text_file, 'w')
            for each_path in cache_dialog_path:
                if os.path.exists(each_path):
                    write_cache_text(each_path, f)
            f.close()
            LOG.debug("Completed generating cache")
        else:
            LOG.debug("Cache file 'cache_text.txt' already exists")
    except Exception:
        LOG.error("Could not open text file to write cache")


def write_cache_text(cache_path, f):
    for file in glob.glob(cache_path + "/*.dialog"):
        try:
            with open(file, 'r') as fp:
                all_dialogs = fp.readlines()
                for each_dialog in all_dialogs:
                    # split the sentences
                    each_dialog = re.split(
                        r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\;|\?)\s',
                        each_dialog.strip())
                    for each in each_dialog:
                        if (REGEX_SPL_CHARS.search(each) is None):
                            # Do not consider sentences with special
                            # characters other than any punctuation
                            # ex : <<< LOADING <<<
                            # should not be considered
                            f.write(each.strip() + '\n')
        except Exception:
            # LOG.debug("Dialog Skipped")
            pass


def download_audio(cache_audio_dir, cache_text_file):
    """
    This method takes the sentences from the text file generated
    using generate_cache_text() and performs TTS inference on
    mimic2-api. The wav files and phonemes are stored in
    'cache_audio_dir'
    Args:
        cache_audio_dir (path): path to store .wav files
        cache_text_file (file): file containing the sentences
    """
    if os.path.isfile(cache_text_file) and \
            os.path.exists(cache_audio_dir):
        if not os.listdir(cache_audio_dir):
            session = FuturesSession()
            with open(cache_text_file, 'r') as fp:
                all_dialogs = fp.readlines()
                for each_dialog in all_dialogs:
                    each_dialog = each_dialog.strip()
                    key = str(hashlib.md5(
                        each_dialog.encode('utf-8', 'ignore')).hexdigest())
                    wav_file = os.path.join(cache_audio_dir, key + '.wav')
                    each_dialog = parse.quote(each_dialog)

                    mimic2_url = MIMIC2_URL + each_dialog + '&visimes=True'
                    try:
                        req = session.get(mimic2_url)
                        results = req.result().json()
                        audio = base64.b64decode(results['audio_base64'])
                        vis = results['visimes']
                        if audio:
                            with open(wav_file, 'wb') as audiofile:
                                audiofile.write(audio)
                        if vis:
                            pho_file = os.path.join(cache_audio_dir,
                                                    key + ".pho")
                            with open(pho_file, "w") as cachefile:
                                cachefile.write(json.dumps(vis))  # Mimic2
                                # cachefile.write(str(vis))  # Mimic
                    except Exception as e:
                        # Skip this dialog and continue
                        LOG.error("Unable to get pre-loaded cache "
                                  "due to ({})".format(repr(e)))

            LOG.debug("Completed getting cache for {}".format(TTS))

        else:
            LOG.debug("Pre-loaded cache for {} already exists".
                      format(TTS))
    else:
        missing_path = cache_text_file if not \
            os.path.isfile(cache_text_file)\
            else cache_audio_dir
        LOG.error("Path ({}) does not exist for getting the cache"
                  .format(missing_path))


def copy_cache(cache_audio_dir):
    """
    This method copies the cache from 'cache_audio_dir'
    to TTS specific cache directory given by
    get_cache_directory()
    Args:
        cache_audio_dir (path): path containing .wav files
    """
    if os.path.exists(cache_audio_dir):
        # get tmp directory where tts cache is stored
        dest = get_cache_directory('tts/' + 'Mimic2')
        files = os.listdir(cache_audio_dir)
        for f in files:
            shutil.copy2(os.path.join(cache_audio_dir, f), dest)
        LOG.debug("Copied all pre-loaded cache for {} to {}"
                  .format(TTS, dest))
    else:
        LOG.debug("No Source directory for {} pre-loaded cache"
                  .format(TTS))


# Start here
def main(cache_audio_dir):
    # Path where cache is stored and not cleared on reboot/TTS change
    if cache_audio_dir:
        cache_text_file = os.path.join(cache_audio_dir,
                                       '..', 'cache_text.txt')
        generate_cache_text(cache_audio_dir, cache_text_file)
        download_audio(cache_audio_dir, cache_text_file)
        copy_cache(cache_audio_dir)
