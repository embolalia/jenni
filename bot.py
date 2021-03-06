#!/usr/bin/env python
# coding=utf-8
"""
bot.py - Willie IRC Bot
Copyright 2008, Sean B. Palmer, inamidst.com
Copyright 2012, Edward Powell, http://embolalia.net
Copyright © 2012, Elad Alfassa <elad@fedoraproject.org>

Licensed under the Eiffel Forum License 2.

http://willie.dftba.net/
"""

import time, sys, os, re, threading, imp
import irc
from settings import SettingsDB
from tools import try_print_stderr as stderr

home = os.getcwd()
modules_dir = os.path.join(home, 'modules')

def decode(bytes):
    try: text = bytes.decode('utf-8')
    except UnicodeDecodeError:
        try: text = bytes.decode('iso-8859-1')
        except UnicodeDecodeError:
            text = bytes.decode('cp1252')
    return text

def enumerate_modules(config):
    filenames = []
    if not hasattr(config, 'enable') or not config.enable:
        for fn in os.listdir(modules_dir):
            if fn.endswith('.py') and not fn.startswith('_'):
                filenames.append(os.path.join(modules_dir, fn))
    else:
        for fn in config.enable:
            filenames.append(os.path.join(modules_dir, fn + '.py'))

    if hasattr(config, 'extra') and config.extra is not None:
        for fn in config.extra:
            if os.path.isfile(fn):
                filenames.append(fn)
            elif os.path.isdir(fn):
                for n in os.listdir(fn):
                    if n.endswith('.py') and not n.startswith('_'):
                        filenames.append(os.path.join(fn, n))
    return filenames

class Willie(irc.Bot):
    def __init__(self, config):
        if hasattr(config, "logchan_pm"): lc_pm = config.logchan_pm
        else: lc_pm = None
        args = (config.nick, config.name, config.channels, config.password, lc_pm)
        irc.Bot.__init__(self, *args)
        self.config = config
        """The ``Config`` for the current Willie instance."""
        self.doc = {}
        """
        A dictionary of module functions to their docstring and example, if
        declared.
        """
        self.stats = {}
        """
        A dictionary which maps a tuple of a function name and where it was used
        to the nuber of times it was used there.
        """
        self.times = {}
        """
        A dictionary mapping lower-case'd nicks to dictionaries which map
        funtion names to the time which they were last used by that nick.
        """
        self.acivity = {}
        
        self.setup()
        self.settings = SettingsDB(config)

    def setup(self):
        stderr("\nWelcome to Willie. Loading modules...\n\n")
        self.variables = {}


        filenames = enumerate_modules(self.config)
        self.enumerate_modules = enumerate_modules

        os.sys.path.insert(0,modules_dir) 
        modules = []
        excluded_modules = getattr(self.config, 'exclude', [])
        error_count = 0
        for filename in filenames:
            name = os.path.basename(filename)[:-3]
            if name in excluded_modules: continue
            try: module = imp.load_source(name, filename)
            except Exception, e:
                error_count = error_count + 1
                stderr("Error loading %s: %s (in bot.py)" % (name, e))
            else:
                try:
                    if hasattr(module, 'setup'):
                        module.setup(self)
                    self.register(vars(module))
                    modules.append(name)
                except Exception, e:
                    error_count = error_count + 1
                    stderr("Error in %s setup procedure: %s (in bot.py)" % (name, e))

        if modules:
            stderr('\n\nRegistered %d modules,' % len(modules))
            stderr('%d modules failed to load\n\n' % error_count)
        else: stderr("Warning: Couldn't find any modules")

        self.bind_commands()

    def register(self, variables):
        """
        With the ``__dict__`` attribute from a Willie module, update or add the
        trigger commands and rules to allow the function to be triggered.
        """
        # This is used by reload.py, hence it being methodised
        for name, obj in variables.iteritems():
            if hasattr(obj, 'commands') or hasattr(obj, 'rule'):
                self.variables[name] = obj

    def bind_commands(self):
        self.commands = {'high': {}, 'medium': {}, 'low': {}}

        def bind(self, priority, regexp, func):
            # register documentation
            if not hasattr(func, 'name'):
                func.name = func.__name__
            if func.__doc__:
                if hasattr(func, 'example'):
                    example = func.example
                    example = example.replace('$nickname', self.nick)
                else: example = None
                self.doc[func.name] = (func.__doc__, example)
            self.commands[priority].setdefault(regexp, []).append(func)

        def sub(pattern, self=self):
            # These replacements have significant order
            pattern = pattern.replace('$nickname', r'%s' % re.escape(self.nick))
            return pattern.replace('$nick', r'%s[,:] +' % re.escape(self.nick))

        for name, func in self.variables.iteritems():
            # print name, func
            if not hasattr(func, 'priority'):
                func.priority = 'medium'

            if not hasattr(func, 'thread'):
                func.thread = True

            if not hasattr(func, 'event'):
                func.event = 'PRIVMSG'
            else: func.event = func.event.upper()

            if not hasattr(func, 'rate'):
                if hasattr(func, 'commands'):
                    func.rate = 0
                else:
                    func.rate = 0

            if hasattr(func, 'rule'):
                if isinstance(func.rule, str):
                    pattern = sub(func.rule)
                    regexp = re.compile(pattern, re.I)
                    bind(self, func.priority, regexp, func)

                if isinstance(func.rule, tuple):
                    # 1) e.g. ('$nick', '(.*)')
                    if len(func.rule) == 2 and isinstance(func.rule[0], str):
                        prefix, pattern = func.rule
                        prefix = sub(prefix)
                        regexp = re.compile(prefix + pattern, re.I)
                        bind(self, func.priority, regexp, func)

                    # 2) e.g. (['p', 'q'], '(.*)')
                    elif len(func.rule) == 2 and isinstance(func.rule[0], list):
                        prefix = self.config.prefix
                        commands, pattern = func.rule
                        for command in commands:
                            command = r'(%s)\b(?: +(?:%s))?' % (command, pattern)
                            regexp = re.compile(prefix + command, re.I)
                            bind(self, func.priority, regexp, func)

                    # 3) e.g. ('$nick', ['p', 'q'], '(.*)')
                    elif len(func.rule) == 3:
                        prefix, commands, pattern = func.rule
                        prefix = sub(prefix)
                        for command in commands:
                            command = r'(%s) +' % command
                            regexp = re.compile(prefix + command + pattern, re.I)
                            bind(self, func.priority, regexp, func)

            if hasattr(func, 'commands'):
                for command in func.commands:
                    template = r'^%s(%s)(?: +(.*))?$'
                    pattern = template % (self.config.prefix, command)
                    regexp = re.compile(pattern, re.I)
                    bind(self, func.priority, regexp, func)

    def wrapped(self, origin, text, match):
        class WillieWrapper(object):
            def __init__(self, willie):
                self.bot = willie

            def __getattr__(self, attr):
                sender = origin.sender or text
                if attr == 'reply':
                    return (lambda msg:
                        self.bot.msg(sender, origin.nick + ': ' + msg))
                elif attr == 'say':
                    return lambda msg: self.bot.msg(sender, msg)
                elif attr == 'action':
                    return lambda msg: self.bot.msg(sender, '\001ACTION '+msg+'\001')
                return getattr(self.bot, attr)

        return WillieWrapper(self)
    class Trigger(unicode):
        def __new__(cls, text, origin, bytes, match, event, args, self):
            s = unicode.__new__(cls, text)
            s.sender = origin.sender
            """
            The channel (or nick, in a private message) from which the
            message was sent.
            """
            s.nick = origin.nick
            """The nick of the person who sent the message."""
            s.event = event
            """The event which triggered the message."""#TODO elaborate
            s.bytes = bytes
            """The line which triggered the message"""#TODO elaborate
            s.match = match
            """
            The regular expression ``MatchObject_`` for the triggering line.
            .. _MatchObject: http://docs.python.org/library/re.html#match-objects
            """
            s.group = match.group
            """The ``group`` function of the ``match`` attribute.
            
            See Python ``re_`` documentation for details."""
            s.groups = match.groups
            """The ``groups`` function of the ``match`` attribute.
            
            See Python ``re_`` documentation for details."""
            s.args = args
            """The arguments given to a command.""" #TODO elaborate
            s.admin = (origin.nick in self.config.admins) or origin.nick.lower() == self.config.owner.lower()
            """
            True if the nick which triggered the command is in Willie's admin
            list as defined in the config file.
            """
                
            if s.admin == False:
                for each_admin in self.config.admins:
                    re_admin = re.compile(each_admin)
                    if re_admin.findall(origin.host):
                        s.admin = True
                    elif '@' in each_admin:
                        temp = each_admin.split('@')
                        re_host = re.compile(temp[1])
                        if re_host.findall(origin.host):
                            s.admin = True
            s.owner = origin.nick + '@' + origin.host == self.config.owner
            if s.owner == False: s.owner = origin.nick == self.config.owner
            s.host = origin.host
            if s.sender is not s.nick: #no ops in PM
                try:
                    s.ops = self.ops[s.sender]
                except:
                    s.ops = []
                """List of channel operators in the channel the message was recived in"""
                try:
                    s.halfplus = self.halfplus[s.sender]
                except:
                    s.halfplus = []
                """List of channel half-operators in the channel the message was recived in"""
                s.isop = (s.nick.lower() in s.ops or s.nick.lower() in s.halfplus)
                """True if the user is half-op or an op"""
            else:
                s.isop = False
                s.ops = []
                s.halfplus = []
            return s

    def call(self, func, origin, willie, trigger):
        nick = (trigger.nick).lower()
        if nick in self.times:
            if func in self.times[nick]:
                if not trigger.admin:
                    timediff = time.time() - self.times[nick][func]
                    if timediff < func.rate:
                        self.times[nick][func] = time.time()
                        self.debug('bot.py', "%s prevented from using %s in %s: %d < %d" % (trigger.nick, func.__name__, trigger.sender, timediff, func.rate), "warning")
                        return
        else: self.times[nick] = dict()
        self.times[nick][func] = time.time()
        try:
            func(willie, trigger)
        except Exception, e:
            self.error(origin, trigger)

    def limit(self, origin, func):
        if origin.sender and origin.sender.startswith('#'):
            if hasattr(self.config, 'limit'):
                limits = self.config.limit.get(origin.sender)
                if limits and (func.__module__ not in limits):
                    return True
        return False

    def dispatch(self, origin, args):
        bytes, event, args = args[0], args[1], args[2:]
        text = decode(bytes)

        for priority in ('high', 'medium', 'low'):
            items = self.commands[priority].items()
            for regexp, funcs in items:
                for func in funcs:
                    if event != func.event: continue

                    match = regexp.match(text)
                    if match:
                        if self.limit(origin, func): continue

                        willie = self.wrapped(origin, text, match)
                        trigger = self.Trigger(text, origin, bytes, match, event, args, self)
                        if trigger.nick in self.config.other_bots: continue

                        nick = (trigger.nick).lower()

                        ## blocking ability
                        if os.path.isfile("blocks"):
                            g = open("blocks", "r")
                            contents = g.readlines()
                            g.close()

                            try: bad_masks = contents[0].split(',')
                            except: bad_masks = ['']

                            try: bad_nicks = contents[1].split(',')
                            except: bad_nicks = ['']

                            if len(bad_masks) > 0:
                                for hostmask in bad_masks:
                                    hostmask = hostmask.replace("\n", "")
                                    if len(hostmask) < 1: continue
                                    re_temp = re.compile(hostmask)
                                    host = origin.host
                                    host = host.lower()
                                    if re_temp.findall(host) or hostmask in host:
                                        return
                            if len(bad_nicks) > 0:
                                for nick in bad_nicks:
                                    nick = nick.replace("\n", "")
                                    if len(nick) < 1: continue
                                    re_temp = re.compile(nick)
                                    if re_temp.findall(trigger.nick) or nick in trigger.nick:
                                        return
                        # stats
                        if func.thread:
                            targs = (func, origin, willie, trigger)
                            t = threading.Thread(target=self.call, args=targs)
                            t.start()
                        else: self.call(func, origin, willie, trigger)

                        for source in [origin.sender, origin.nick]:
                            try: self.stats[(func.name, source)] += 1
                            except KeyError:
                                self.stats[(func.name, source)] = 1
    def debug(self, tag, text, level):
        """
        Sends an error to Willie's configured ``debug_target``. 
        """
        if not self.config.verbose:
            self.config.verbose = 'warning'
        elif not (self.config.debug_target == 'stdio' or self.config.debug_target.startswith('#')):
            self.config.debug_target = 'stdio'
        debug_msg = "[%s] %s" % (tag, text)
        if level == 'verbose':
            if self.config.verbose == 'verbose':
                if (self.config.debug_target == 'stdio'):
                    print debug_msg
                else:
                    self.msg(self.config.debug_target, debug_msg)
                return True
        elif level == 'warning':
            if self.config.verbose == 'verbose' or self.config.verbose == 'warning':
                if (self.config.debug_target == 'stdio'):
                    print debug_msg
                else:
                    self.msg(self.config.debug_target, debug_msg)
                return True
        elif level == 'always':
            if (self.config.debug_target == 'stdio'):
                print debug_msg
            else:
                self.msg(self.config.debug_target, debug_msg)
            return True
        
        return False

if __name__ == '__main__':
    print __doc__
