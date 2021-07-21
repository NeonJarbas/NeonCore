from inspect import signature

from mycroft.messagebus import MessageBusClient
from mycroft.skills.skill_manager import SkillManager, _shutdown_skill
from mycroft.util import connected
from mycroft.util.log import LOG
from neon_core.skills.skill_loader import PluginSkillLoader, find_skill_plugins
from neon_core.skills.skill_store import SkillsStore


class NeonSkillManager(SkillManager):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skill_downloader = SkillsStore(skills_dir=self.msm.skills_dir,
                                            bus=self.bus)
        self.skill_downloader.skills_dir = self.msm.skills_dir
        self.plugin_skills = {}

    def download_or_update_defaults(self):
        # on launch only install if missing, updates handled separately
        # if osm is disabled in .conf this does nothing
        if self.config["skills"]["auto_update"]:
            try:
                self.skill_downloader.install_default_skills()
            except Exception as e:
                if connected():
                    # if there is internet log the error
                    LOG.exception(e)
                    LOG.error("default skills installation failed")
                else:
                    # if no internet just skip this update
                    LOG.error(
                        "no internet, skipped default skills installation")

    def _load_new_skills(self):
        super()._load_new_skills()
        plugin_skills = []
        plugins = find_skill_plugins()
        for skill_id, plug in plugins.items():
            if skill_id not in self.plugin_skills:
                self._load_plugin_skill(skill_id, plug)

    def _load_plugin_skill(self, skill_id, skill_plugin):
        if not self.config["websocket"].get("shared_connection", True):
            # see BusBricker skill to understand why this matters
            # any skill can manipulate the bus from other skills
            # this patch ensures each skill gets it's own
            # connection that can't be manipulated by others
            # https://github.com/EvilJarbas/BusBrickerSkill
            bus = MessageBusClient()
            bus.run_in_thread()
        else:
            bus = self.bus

        skill_loader = PluginSkillLoader(bus, skill_id)

        try:
            load_status = skill_loader.load(skill_plugin)
        except Exception:
            LOG.exception(f'Load of skill {skill_id} failed!')
            load_status = False
        finally:
            self.plugin_skills[skill_id] = skill_loader

        return skill_loader if load_status else None

    def stop(self):
        """Tell the manager to shutdown."""
        super().stop()
        # Do a clean shutdown of all plugin skills
        for skill_loader in self.plugin_skills.values():
            if skill_loader.instance is not None:
                _shutdown_skill(skill_loader.instance)

    def run(self):
        """Load skills and update periodically from disk and internet."""
        self.download_or_update_defaults()
        super().run()

    def handle_converse_request(self, message):
        """Check if the targeted skill id can handle conversation

        If supported, the conversation is invoked.
        """
        skill_id = message.data['skill_id']

        def _converse(skill_loader):
            try:
                # check the signature of a converse method
                # to either pass a message or not
                if len(signature(
                        skill_loader.instance.converse).parameters) == 1:
                    result = skill_loader.instance.converse(
                        message=message)
                else:
                    utterances = message.data['utterances']
                    lang = message.data['lang']
                    result = skill_loader.instance.converse(
                        utterances=utterances, lang=lang)
                self._emit_converse_response(result, message, skill_loader)
            except Exception:
                error_message = 'exception in converse method'
                LOG.exception(error_message)
                self._emit_converse_error(message, skill_id, error_message)

        if skill_id in self.plugin_skills:
            skill_found = True
            _converse(self.plugin_skills[skill_id])
        else:
            # loop trough skills list and call converse for skill with skill_id
            skill_found = False
            for skill_loader in self.skill_loaders.values():
                if skill_loader.skill_id == skill_id:
                    skill_found = True
                    if not skill_loader.loaded:
                        error_message = 'converse requested but skill not loaded'
                        self._emit_converse_error(message, skill_id,
                                                  error_message)
                    else:
                        _converse(skill_loader)
                    break
            else:
                error_message = 'skill id does not exist'
                self._emit_converse_error(message, skill_id, error_message)

    def _emit_converse_error(self, message, skill_id, error_msg):
        super()._emit_converse_error(message, skill_id, error_msg)
        # Also emit the old error message to keep compatibility and for any
        # listener on the bus
        reply = message.reply('skill.converse.error',
                              data=dict(skill_id=skill_id, error=error_msg))
        self.bus.emit(reply)

    def send_skill_list(self, _):
        """Send list of loaded skills."""
        try:
            message_data = {}
            for skill_dir, skill_loader in self.skill_loaders.items():
                message_data[skill_loader.skill_id] = dict(
                    active=skill_loader.active and skill_loader.loaded,
                    id=skill_loader.skill_id
                )
            for skill_id, skill_loader in self.plugin_skills.items():
                message_data[skill_id] = dict(
                    active=skill_loader.active and skill_loader.loaded,
                    id=skill_id
                )
            self.bus.emit(Message('mycroft.skills.list', data=message_data))
        except Exception:
            LOG.exception('Failed to send skill list')

    def activate_skill(self, message):
        """Activate a deactivated skill."""
        super().activate_skill(message)
        try:
            for skill_loader in self.plugin_skills.values():
                if (message.data['skill'] in ('all', skill_loader.skill_id) and
                        not skill_loader.active):
                    skill_loader.activate()
        except Exception:
            LOG.exception('Couldn\'t activate skill')

    def deactivate_skill(self, message):
        """Deactivate a skill."""
        super().deactivate_skill(message)
        try:
            for skill_loader in self.plugin_skills.values():
                if message.data['skill'] == skill_loader.skill_id:
                    skill_loader.deactivate()
        except Exception:
            LOG.exception('Failed to deactivate ' + message.data['skill'])

    def deactivate_except(self, message):
        """Deactivate all skills except the provided."""
        try:
            skill_to_keep = message.data['skill']
            LOG.info('Deactivating all skills except {}'.format(skill_to_keep))
            loaded_skill_file_names = [
                                          os.path.basename(skill_dir) for
                                          skill_dir in self.skill_loaders
                                      ] + list(self.plugin_skills.keys())
            if skill_to_keep in loaded_skill_file_names:
                for skill in self.skill_loaders.values():
                    if skill.skill_id != skill_to_keep:
                        skill.deactivate()
                for skill in self.plugin_skills.values():
                    if skill.skill_id != skill_to_keep:
                        skill.deactivate()
            else:
                LOG.info('Couldn\'t find skill ' + message.data['skill'])
        except Exception:
            LOG.exception('An error occurred during skill deactivation!')
