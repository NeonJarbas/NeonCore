from time import time

from mycroft.skills.skill_loader import SkillLoader
from mycroft.util.log import LOG
from ovos_plugin_manager.utils import find_plugins, PluginTypes
from ovos_utils.messagebus import get_mycroft_bus
from ovos_utils.log import LOG


def find_skill_plugins():
    #return find_plugins(PluginTypes.SKILL)  # TODO
    return find_plugins("holmes.plugin.skill")


class PluginSkillLoader(SkillLoader):
    def __init__(self, bus, skill_id):
        super().__init__(bus, skill_id)
        self.skill_directory = skill_id
        self.skill_id = skill_id

    def reload_needed(self):
        return False

    def _create_skill_instance(self, skill_module):
        """Use v2 skills framework to create the skill."""
        try:
            self.instance = skill_module()
        except Exception as e:
            log_msg = 'Skill __init__ failed with {}'
            LOG.exception(log_msg.format(repr(e)))
            self.instance = None

        if self.instance:
            self.instance.skill_id = self.skill_id
            self.instance.bind(self.bus)
            try:
                self.instance.load_data_files()
                # Set up intent handlers
                # TODO: can this be a public method?
                self.instance._register_decorated()
                self.instance.register_resting_screen()
                self.instance.initialize()
                self.skill_directory = self.instance.root_dir
            except Exception as e:
                LOG.exception(f'Skill initialization failed: {e}')
                # If an exception occurs, attempt to clean up the skill
                try:
                    self.instance.default_shutdown()
                except Exception as e2:
                    # if initialize failed then it's likely
                    # default_shutdown will fail
                    LOG.debug(f'Skill cleanup failed: {e2}')
                    LOG.debug(f'this is usually fine and often expected')
                self.instance = None

        return self.instance is not None

    def load(self, skill_module):
        self._prepare_for_load()
        if self.is_blacklisted:
            self._skip_load()
        else:
            if self._create_skill_instance(skill_module):
                self._check_for_first_run()
                self.loaded = True

        self.last_loaded = time()
        self._communicate_load_status()
        if self.loaded:
            self._prepare_settings_meta()
        return self.loaded
