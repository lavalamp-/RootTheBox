# -*- coding: utf-8 -*-
'''
Created on Sep 20, 2012

@author: moloch

    Copyright 2012 Root the Box

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
'''


import re
import logging

from os import urandom
from datetime import datetime
from libs.Singleton import Singleton
from libs.ConfigManager import ConfigManager
from sqlalchemy import Column, create_engine
from sqlalchemy.sql import and_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import DateTime, Integer, Unicode
from sqlalchemy.ext.declarative import declared_attr, declarative_base


class MemoryDatabaseObject(object):
    '''
    Base object for in-memory database
    '''

    @declared_attr
    def __tablename__(self):
        ''' Converts class name from camel case to snake case '''
        name = self.__name__
        return unicode(
            name[0].lower() +
            re.sub(r'([A-Z])',
                   lambda letter: "_" + letter.group(0).lower(), name[1:])
        )
    
    id = Column(Integer, primary_key=True, unique=True, nullable=False)
    created = Column(DateTime, default=datetime.now)


MemoryBaseObject = declarative_base(cls=MemoryDatabaseObject)


class Bot(MemoryBaseObject):
    ''' Bot Class '''

    wsock_uuid = Column(Unicode(36), nullable=False)
    team_uuid  = Column(Unicode(36), nullable=False)
    team_name  = Column(Unicode(64), nullable=False)
    box_uuid   = Column(Unicode(36), nullable=False)
    box_name   = Column(Unicode(64), nullable=False)
    remote_ip  = Column(Unicode(36), nullable=False)
    total_reward = Column(Integer, default=0, nullable=False)


@Singleton
class BotManager(object):
    '''
    Holds refs to botnet web socket objects.
    '''

    def __init__(self):
        config = ConfigManager.Instance()
        self.botnet = {}  # Holds refs to wsockets
        self.monitors = {}
        self.sqlite_engine = create_engine(u'sqlite://')
        setattr(self.sqlite_engine, 'echo', config.bot_sql)
        Session = sessionmaker(bind=self.sqlite_engine, autocommit=True)
        self.botdb = Session(autoflush=True)
        MemoryBaseObject.metadata.create_all(self.sqlite_engine)

    def all(self):
        bots = self.botdb.query(Bot).all()
        return [self.botnet[bot.wsock_uuid] for bot in bots]

    def by_box(self, box):
        bots = self.botdb.query(Bot).filter_by(box_uuid=unicode(box.uuid)).all()
        return [self.botnet[bot.wsock_uuid] for bot in bots]

    def by_team(self, team):
        bots = self.botdb.query(Bot).filter_by(team_name=unicode(team)).all()
        return [self.botnet[bot.wsock_uuid] for bot in bots]

    def count_by_team(self, team):
        return len(self.by_team(team))

    def count_by_team_uuid(self, tuuid):
        return self.botdb.query(Bot).filter_by(team_uuid=unicode(tuuid)).count()

    def add_bot(self, bot_wsocket):
        if not self.is_duplicate(bot_wsocket):
            bot = Bot(
                wsock_uuid=unicode(bot_wsocket.uuid),
                team_name=unicode(bot_wsocket.team_name),
                team_uuid=unicode(bot_wsocket.team_uuid),
                box_name=unicode(bot_wsocket.box_name),
                box_uuid=unicode(bot_wsocket.box_uuid),
                remote_ip=unicode(bot_wsocket.remote_ip)
            )
            self.botdb.add(bot)
            self.botdb.flush()
            self.botnet[bot_wsocket.uuid] = bot_wsocket
            self.notify_monitors(bot.team_name)
            return True
        else:
            return False

    def remove_bot(self, bot_wsocket):
        bot = self.botdb.query(Bot).filter_by(wsock_uuid=unicode(bot_wsocket.uuid)).first()
        if bot is not None:
            logging.debug("Removing bot '%s' at %s" % (bot.team_uuid, bot.remote_ip))
            team = bot.team_name
            self.botnet.pop(bot_wsocket.uuid, None)
            self.botdb.delete(bot)
            self.botdb.flush()
            self.notify_monitors(team)
        else:
            logging.warn("Failed to remove bot '%s' does not exist in manager" % bot_wsocket.uuid)

    def is_duplicate(self, bot_wsocket):
        ''' Check for duplicate bots '''
        assert(bot_wsocket.team_uuid is not None)
        assert(bot_wsocket.box_uuid is not None)
        return 0 < self.botdb.query(Bot).filter(
            and_(Bot.team_uuid == unicode(bot_wsocket.team_uuid), Bot.box_uuid == unicode(bot_wsocket.box_uuid))
        ).count()

    def add_monitor(self, monitor_wsocket):
        ''' Add new monitor socket '''
        if monitor_wsocket.team_name not in self.monitors:
            self.monitors[monitor_wsocket.team_name] = []
        self.monitors[monitor_wsocket.team_name].append(monitor_wsocket)

    def remove_monitor(self, monitor_wsocket):
        ''' Remove a monitor socket '''
        if monitor_wsocket.team_name in self.monitors and monitor_wsocket in self.monitors[monitor_wsocket.team_name]:
            self.monitors[monitor_wsocket.team_name].remove(monitor_wsocket)

    def notify_monitors(self, team_name):
        ''' Update team monitors '''
        if team_name in self.monitors and 0 < len(self.monitors[team_name]):
            logging.debug("Sending update to %s" % team_name)
            boxes = self.get_boxes(team_name)
            for monitor in self.monitors[team_name]:
                monitor.update(boxes)

    def get_boxes(self, team):
        ''' Get info on boxes for a team '''
        bots = self.botdb.query(Bot).filter_by(team_name=unicode(team)).all()
        boxes = []
        for bot in bots:
            boxes.append(
                [bot.remote_ip, bot.box_name, bot.total_reward]
            )
        return boxes

    def add_rewards(self, team, reward):
        ''' Add rewards to bot records '''
        bots = self.botdb.query(Bot).filter_by(team_name=unicode(team)).all()
        for bot in bots:
            bot.total_reward += reward
            self.botdb.add(bot)
            self.botdb.flush()


def ping_bots():
    ''' Ping all websockets in database '''
    bot_manager = BotManager.Instance()
    logging.info("Pinging open botnet websockets")
    for bot in bot_manager.all():
        bot.ping(urandom(8).encode('hex'))
    for muuid in bot_manager.monitors:
        for monitor in bot_manager.monitors[muuid]:
            monitor.ping(urandom(8).encode('hex'))