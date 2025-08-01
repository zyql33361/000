import os
import re
import threading
import time
from datetime import datetime, timedelta

import inflection
from cached_property import cached_property

from module.base.decorator import del_cached_property
from module.config.config import AzurLaneConfig, TaskEnd
from module.config.deep import deep_get, deep_set
from module.exception import *
from module.logger import logger
from module.notify import handle_notify
from module.gg_handler.gg_handler import GGHandler


class AzurLaneAutoScript:
    stop_event: threading.Event = None
    GameRestartBecauseErrorTimes = 0
    AutoRestart_Enabled = False
    AutoRestart_AttemptsToRestart = 0
    AutoRestart_NotifyWhenAutoRestart = False

    def __init__(self, config_name='alas'):
        logger.hr('Start', level=0)
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}

        self.class_name = self.__class__.__name__
        self.is_azur = False
        self.is_ark = False
        if self.class_name == "AzurLaneAutoScript":
            self.is_azur = True
        elif self.class_name == "ArknightsAutoScript":
            self.is_ark = True

    @cached_property
    def config(self):
        try:
            config = AzurLaneConfig(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')  #解决部分不推送报错
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> RequestHumanTakeover",
            )
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self):
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            handle_notify(                              #解决部分不推送报错（如模拟器未开启
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> RequestHumanTakeover",
            )
            exit(1)
        except EmulatorNotRunningError:
            logger.critical('EmulatorNotRunningError')
            exit(1)
        except EmulatorNotRunningError:
            logger.critical('EmulatorNotRunningError')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        try:
            from module.server_checker import ServerChecker
            checker = ServerChecker(server=self.config.Emulator_ServerName)
            return checker
        except Exception as e:
            logger.exception(e)
            exit(1)

    def run(self, command, skip_first_screenshot=False):
        self.AutoRestart_Enabled = deep_get(self.config.data, "Restart.AutoRestart.Enabled")
        self.AutoRestart_NotifyWhenAutoRestart = deep_get(self.config.data, "Restart.AutoRestart.NotifyWhenAutoRestart")
        self.AutoRestart_AttemptsToRestart = deep_get(self.config.data, "Restart.AutoRestart.AttemptsToRestart")
        try:
            if not skip_first_screenshot:
                self.device.screenshot()
            self.__getattribute__(command)()
            if command != "restart" and self.GameRestartBecauseErrorTimes != 0:
                self.GameRestartBecauseErrorTimes = 0
            return True
        except RequireRestartGame:
            self.config.task_call('Restart')
            self.device.sleep(10)
            return True
        except TaskEnd:
            return True
        except GameNotRunningError as e:
            logger.warning(e)
            self.config.task_call('Restart')
            return False
        except (GameStuckError, GameTooManyClickError) as e:
            logger.error(e)
            self.save_error_log()
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            from module.handler.info_handler import InfoHandler
            info_handler = InfoHandler(config=self.config,device=self.device)
            info_handler.handle_urgent_commission()
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GameBugError as e:
            logger.warning(e)
            self.save_error_log()
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GamePageUnknownError:
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            self.checker.check_now()
            if self.checker.is_available():
                if self.AutoRestart_Enabled and self.GameRestartBecauseErrorTimes <= self.AutoRestart_AttemptsToRestart:
                    if self.AutoRestart_NotifyWhenAutoRestart:
                        handle_notify(
                            self.config.Error_OnePushConfig,
                            title=f"Alas <{self.config_name}> auto restarted",
                            content=f"Command \"{command}\" failed because GamePageUnknownError, but alas auto restarted",
                        )
                    self.config.task_call('Restart')
                    self.GameRestartBecauseErrorTimes += 1
                    self.device.sleep(10)
                    return False
                else:
                    self.GameRestartBecauseErrorTimes = 0
                    logger.critical('Game page unknown')
                    self.save_error_log()
                    handle_notify(
                        self.config.Error_OnePushConfig,
                        title=f"Alas <{self.config_name}> crashed",
                        content=f"<{self.config_name}> GamePageUnknownError",
                    )
                    logger.info('Restart to reset Game page in 10 seconds')
                    self.device.sleep(10)
                    from module.handler.login import LoginHandler
                    LoginHandler(self.config, self.device).app_restart()
                    return False
            else:
                self.checker.wait_until_available()
                return False
        except ScriptError as e:
            logger.exception(e)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> ScriptError",
            )
            exit(1)
        except RequestHumanTakeover:
            if self.AutoRestart_Enabled and self.GameRestartBecauseErrorTimes <= self.AutoRestart_AttemptsToRestart:
                if self.AutoRestart_NotifyWhenAutoRestart:
                    handle_notify(
                        self.config.Error_OnePushConfig,
                        title=f"Alas <{self.config_name}> auto restarted",
                        content=f"Command \"{command}\" failed because RequestHumanTakeover, but alas auto restarted",
                    )
                self.config.task_call('Restart')
                self.GameRestartBecauseErrorTimes += 1
                self.device.sleep(10)
                return False
            else:
                self.GameRestartBecauseErrorTimes = 0
                logger.critical('Request human takeover')
                handle_notify(
                    self.config.Error_OnePushConfig,
                    title=f"Alas <{self.config_name}> crashed",
                    content=f"<{self.config_name}> RequestHumanTakeover",
                )
                exit(1)
        except MapWalkError as e:
            if self.AutoRestart_Enabled and self.GameRestartBecauseErrorTimes <= self.AutoRestart_AttemptsToRestart:
                if self.AutoRestart_NotifyWhenAutoRestart:
                    handle_notify(
                        self.config.Error_OnePushConfig,
                        title=f"Alas <{self.config_name}> crashed",
                        content=f"<{self.config_name}> Exception occured",
                    )
                self.config.task_call('Restart')
                self.GameRestartBecauseErrorTimes += 1
                logger.critical(f'left Restart Time: {self.AutoRestart_AttemptsToRestart-self.GameRestartBecauseErrorTimes}')
                self.device.sleep(10)
                return False
            else:
                self.GameRestartBecauseErrorTimes = 0
                logger.critical('MapWalkError')
                logger.exception(e)
                self.save_error_log()
                handle_notify(
                    self.config.Error_OnePushConfig,
                    title=f"Alas <{self.config_name}> crashed",
                    content=f"<{self.config_name}> Exception occured",
                )
                exit(1)                
        except AutoSearchSetError:
            logger.critical('Auto search could not be set correctly. Maybe your ships in hard mode are changed.')
            logger.critical('Request human takeover.')
            exit(1)
        except MapDetectionError as e:
            logger.error(e)
            self.save_error_log()
            logger.warning(f'Game stuck, will be restarted in 10 seconds')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except Exception as e:
            logger.exception(e)
            self.save_error_log()
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> Exception occured",
            )
            exit(1)

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.Error_SaveError:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            folder = f'./log/error/{int(time.time() * 1000)}'
            logger.warning(f'Saving error: {folder}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def restart(self):
        from module.handler.login import LoginHandler
        LoginHandler(self.config, device=self.device).app_restart()

    def start(self):
        from module.handler.login import LoginHandler
        LoginHandler(self.config, device=self.device).app_start()

    def goto_main(self):
        from module.handler.login import LoginHandler
        from module.ui.ui import UI
        if self.device.app_is_running():
            logger.info('App is already running, goto main page')
            UI(self.config, device=self.device).ui_goto_main()
        else:
            logger.info('App is not running, start app and goto main page')
            LoginHandler(self.config, device=self.device).app_start()
            UI(self.config, device=self.device).ui_goto_main()

    def research(self):
        from module.research.research import RewardResearch
        RewardResearch(config=self.config, device=self.device).run()

    def commission(self):
        from module.commission.commission import RewardCommission
        RewardCommission(config=self.config, device=self.device).run()

    def tactical(self):
        from module.tactical.tactical_class import RewardTacticalClass
        RewardTacticalClass(config=self.config, device=self.device).run()

    def dorm(self):
        from module.dorm.dorm import RewardDorm
        RewardDorm(config=self.config, device=self.device).run()

    def meowfficer(self):
        from module.meowfficer.meowfficer import RewardMeowfficer
        RewardMeowfficer(config=self.config, device=self.device).run()

    def guild(self):
        from module.guild.guild_reward import RewardGuild
        RewardGuild(config=self.config, device=self.device).run()

    def reward(self):
        from module.reward.reward import Reward
        Reward(config=self.config, device=self.device).run()

    def awaken(self):
        from module.awaken.awaken import Awaken
        Awaken(config=self.config, device=self.device).run()

    def shop_frequent(self):
        from module.shop.shop_reward import RewardShop
        RewardShop(config=self.config, device=self.device).run_frequent()

    def shop_once(self):
        from module.shop.shop_reward import RewardShop
        RewardShop(config=self.config, device=self.device).run_once()

    def shipyard(self):
        from module.shipyard.shipyard_reward import RewardShipyard
        RewardShipyard(config=self.config, device=self.device).run()

    def gacha(self):
        from module.gacha.gacha_reward import RewardGacha
        RewardGacha(config=self.config, device=self.device).run()

    def freebies(self):
        from module.freebies.freebies import Freebies
        Freebies(config=self.config, device=self.device).run()

    def minigame(self):
        from module.minigame.minigame import Minigame
        Minigame(config=self.config, device=self.device).run()

    def private_quarters(self):
        from module.private_quarters.private_quarters import PrivateQuarters
        PrivateQuarters(config=self.config, device=self.device).run()

    def daily(self):
        from module.daily.daily import Daily
        Daily(config=self.config, device=self.device).run()

    def hard(self):
        from module.hard.hard import CampaignHard
        CampaignHard(config=self.config, device=self.device).run()

    def exercise(self):
        from module.exercise.exercise import Exercise
        Exercise(config=self.config, device=self.device).run()

    def sos(self):
        from module.sos.sos import CampaignSos
        CampaignSos(config=self.config, device=self.device).run()

    def war_archives(self):
        from module.war_archives.war_archives import CampaignWarArchives
        CampaignWarArchives(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def raid_daily(self):
        from module.raid.daily import RaidDaily
        RaidDaily(config=self.config, device=self.device).run()

    def event_a(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_b(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_c(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_d(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_sp(self):
        from module.event.campaign_sp import CampaignSP
        CampaignSP(config=self.config, device=self.device).run()

    def maritime_escort(self):
        from module.event.maritime_escort import MaritimeEscort
        MaritimeEscort(config=self.config, device=self.device).run()

    def opsi_ash_assist(self):
        from module.os_ash.meta import AshBeaconAssist
        AshBeaconAssist(config=self.config, device=self.device).run()

    def opsi_ash_beacon(self):
        from module.os_ash.meta import OpsiAshBeacon
        OpsiAshBeacon(config=self.config, device=self.device).run()

    def opsi_explore(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_explore()

    def opsi_shop(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_shop()

    def opsi_voucher(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_voucher()

    def opsi_daily(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_daily()

    def opsi_obscure(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_obscure()

    def opsi_month_boss(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_month_boss()

    def opsi_abyssal(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_abyssal()

    def opsi_archive(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_archive()

    def opsi_stronghold(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_stronghold()

    def opsi_meowfficer_farming(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_meowfficer_farming()

    def opsi_hazard1_leveling(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_hazard1_leveling()

    def opsi_cross_month(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_cross_month()

    def research_farming_setting(self):
        from module.research_farming.farming import ResearchFarming
        ResearchFarming(config=self.config, device=self.device).run()

    def research_farm(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def research_farm2(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def research_farm3(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def research_farm4(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def research_farm5(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def research_farm6(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def main(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def main2(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def main3(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def event(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def event2(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def event3(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def raid(self):
        from module.raid.run import RaidRun
        RaidRun(config=self.config, device=self.device).run()

    def hospital(self):
        from module.event_hospital.hospital import Hospital
        Hospital(config=self.config, device=self.device).run()

    def coalition(self):
        from module.coalition.coalition import Coalition
        Coalition(config=self.config, device=self.device).run()

    def coalition_sp(self):
        from module.coalition.coalition_sp import CoalitionSP
        CoalitionSP(config=self.config, device=self.device).run()

    def c72_mystery_farming(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def c122_medium_leveling(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def c124_large_leveling(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def gems_farming(self):
        from module.campaign.gems_farming import GemsFarming
        GemsFarming(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def daemon(self):
        from module.daemon.daemon import AzurLaneDaemon
        AzurLaneDaemon(config=self.config, device=self.device, task="Daemon").run()

    def opsi_daemon(self):
        from module.daemon.os_daemon import AzurLaneDaemon
        AzurLaneDaemon(config=self.config, device=self.device, task="OpsiDaemon").run()

    def event_story(self):
        from module.eventstory.eventstory import EventStory
        EventStory(config=self.config, device=self.device, task="EventStory").run()

    def azur_lane_uncensored(self):
        from module.daemon.uncensored import AzurLaneUncensored
        AzurLaneUncensored(config=self.config, device=self.device, task="AzurLaneUncensored").run()

    def benchmark(self):
        from module.daemon.benchmark import run_benchmark
        run_benchmark(config=self.config)

    def game_manager(self):
        from module.daemon.game_manager import GameManager
        GameManager(config=self.config, device=self.device, task="GameManager").run()

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info("Update event detected")
                    logger.info(f"[{self.config_name}] exited. Reason: Update")
                    exit(0)

            time.sleep(5)

            if self.config.should_reload():
                return False

    def get_next_task(self):
        """
        Returns:
            str: Name of the next task.
        """
        while 1:
            task = self.config.get_next()
            self.config.task = task
            self.config.bind(task)

            from module.base.resource import release_resources
            if self.config.task.command != 'Alas':
                release_resources(next_task=task.command)

            if task.next_run > datetime.now():
                logger.info(f'Wait until {task.next_run} for task `{task.command}`')
                self.is_first_task = False
                method = self.config.Optimization_WhenTaskQueueEmpty
                if method == 'close_game':
                    logger.info('Close game during wait')
                    self.device.app_stop()
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                    if task.command != 'Restart':
                        self.config.task_call('Restart')
                        del_cached_property(self, 'config')
                        continue
                elif method == 'goto_main':
                    logger.info('Goto main page during wait')
                    self.run('goto_main')
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                elif method == 'stay_there':
                    logger.info('Stay there during wait')
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                else:
                    logger.warning(f'Invalid Optimization_WhenTaskQueueEmpty: {method}, fallback to stay_there')
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
            break

        AzurLaneConfig.is_hoarding_task = False
        return task.command

    def gg_check(self):
        if deep_get(self.config.data, "GameManager.GGHandler.Enabled"):
            logger.info("GG is enabled, check gg package name")
            if deep_get(self.config.data, "GameManager.GGHandler.GGPackageName") in self.device.list_package():
                logger.info("GG package name exists")
            else:
                logger.critical("GG package name doesn't exist, please check your gg setting")
                logger.critical("友情翻译：你他妈的GG包名填错了，滚去重填！！！")
                exit(1)

    def loop(self):
        self.gg_check()
        logger.set_file_logger(self.config_name)
        logger.info(f'Start scheduler loop: {self.config_name}')
        # Try forced task_call restart to reset GG status
        self.checker.wait_until_available()
        GGHandler(config=self.config, device=self.device).handle_restart_before_tasks()
        check_fail = 0
        while 1:
            # Check update event from GUI
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info("Update event detected")
                    logger.info(f"Alas [{self.config_name}] exited.")
                    break
            # Check game server maintenance
            self.checker.wait_until_available()
            if self.checker.is_recovered():
                # There is an accidental bug hard to reproduce
                # Sometimes, config won't be updated due to blocking
                # even though it has been changed
                # So update it once recovered
                del_cached_property(self, 'config')
                logger.info('Server or network is recovered. Restart game client')
                self.config.task_call('Restart')
            # Get task
            task = self.get_next_task()
            # Init device and change server
            _ = self.device
            self.device.config = self.config

            # Skip first restart
            if task == 'Restart':
                if self.is_first_task:
                    logger.info('Skip task `Restart` at scheduler start')
                else:
                    from module.handler.login import LoginHandler
                    LoginHandler(self.config, self.device).app_restart()
                self.config.task_delay(server_update=True)
                del_cached_property(self, 'config')
                continue

            # Check GG config before a task begins (to reset temporary config), and decide to enable it.
            GGHandler(config=self.config, device=self.device).check_config()
            try:
                GGHandler(config=self.config, device=self.device).check_then_set_gg_status(inflection.underscore(task))
                check_fail = 0
            except GameStuckError:
                del_cached_property(self, 'config')
                check_fail += 1
                if check_fail <= 3:
                    continue
                else:
                    logger.critical('Maybe your emulator died, trying to restart it')
                    self.device.emulator_start()

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            success = True
            if task in ["Research", "Reward", "Commission"]:
                InfiniteDelay = deep_get(self.config.data, f"SomethingSpecial.InfiniteDelay.{task}")
                if InfiniteDelay:
                    logger.warning(f"Task '{task}' infinite delay: {InfiniteDelay}")
                    self.config.task_delay(target=datetime.now() + timedelta(hours=6, seconds=-1), task=task)
                else:
                    success = self.run(inflection.underscore(task))
            else:
                success = self.run(inflection.underscore(task))
            logger.info(f'Scheduler: End task `{task}`')
            self.is_first_task = False

            # Check failures
            failed = deep_get(self.failure_record, keys=task, default=0)
            failed = 0 if success else failed + 1
            deep_set(self.failure_record, keys=task, value=failed)
            if failed >= 3:
                if self.AutoRestart_Enabled and self.GameRestartBecauseErrorTimes <= self.AutoRestart_AttemptsToRestart:
                    failed = 0
                    if self.AutoRestart_NotifyWhenAutoRestart:
                        handle_notify(
                            self.config.Error_OnePushConfig,
                            title=f"Alas <{self.config_name}> auto restarted",
                            content=f"<{task}> failed, but alas auto restarted",
                        )
                    self.config.task_call('Restart')
                    self.GameRestartBecauseErrorTimes += 1
                    self.device.sleep(10)
                else:
                    self.GameRestartBecauseErrorTimes = 0
                    logger.critical(f"Task `{task}` failed 3 or more times.")
                    logger.critical("Possible reason #1: You haven't used it correctly. "
                                    "Please read the help text of the options.")
                    logger.critical("Possible reason #2: There is a problem with this task. "
                                    "Please contact developers or try to fix it yourself.")
                    logger.critical('Request human takeover')
                    handle_notify(
                        self.config.Error_OnePushConfig,
                        title=f"Alas <{self.config_name}> crashed",
                        content=f"<{self.config_name}> RequestHumanTakeover\nTask `{task}` failed 3 or more times.",
                    )
                    exit(1)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.Error_HandleError:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                self.checker.check_now()
                continue
            else:
                break


if __name__ == '__main__':
    alas = AzurLaneAutoScript()
    alas.loop()
