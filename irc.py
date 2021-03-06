#!/usr/bin/env python
# coding=utf-8
"""
irc.py - A Utility IRC Bot
Copyright 2008, Sean B. Palmer, inamidst.com
Copyright 2012, Edward Powell, http://embolalia.net
Copyright © 2012, Elad Alfassa <elad@fedoraproject.org>

Licensed under the Eiffel Forum License 2.

Willie: http://willie.dftba.net/
"""

import sys, re, time, traceback
import socket, asyncore, asynchat
import os, codecs
import traceback
from datetime import datetime

class Origin(object):
    source = re.compile(r'([^!]*)!?([^@]*)@?(.*)')

    def __init__(self, bot, source, args):
        match = Origin.source.match(source or '')
        self.nick, self.user, self.host = match.groups()

        if len(args) > 1:
            target = args[1]
        else: target = None

        mappings = {bot.nick: self.nick, None: None}
        self.sender = mappings.get(target, target)

def create_logdir():
    try: os.mkdir("logs")
    except Exception, e:
        print >> sys.stderr, 'There was a problem creating the logs directory.'
        print >> sys.stderr, e.__class__, str(e)
        print >> sys.stderr, 'Please fix this and then run Willie again.'
        sys.exit(1)

def check_logdir():
    if not os.path.isdir("logs"):
        create_logdir()

def log_raw(line):
    check_logdir()
    f = codecs.open("logs/raw.log", 'a', encoding='utf-8')
    f.write(str(time.time()) + "\t")
    temp = line.replace('\n', '')
    try:
        temp = temp.decode('utf-8')
    except UnicodeDecodeError:
        try:
            temp = temp.decode('iso-8859-1')
        except UnicodeDecodeError:
            temp = temp.decode('cp1252')
    f.write(temp)
    f.write("\n")
    f.close()

class Bot(asynchat.async_chat):
    def __init__(self, nick, name, channels, password=None, logchan_pm=None):
        asynchat.async_chat.__init__(self)
        self.set_terminator('\n')
        self.buffer = ''

        self.nick = nick
        """Willie's current nick. Changing this while Willie is running is untested."""
        self.user = nick
        self.name = name
        """Willie's "real name", as used for whois."""
        self.password = password
        """Willie's NickServ password"""

        self.verbose = True
        """True if Willie is running in verbose mode."""
        self.channels = channels or []
        """The list of channels Willie joins on startup."""
        
        self.stack = []
        self.logchan_pm = logchan_pm

        import threading
        self.sending = threading.RLock()
        
        #Right now, only accounting for two op levels.
        #This might be expanded later.
        #These lists are filled in startup.py, as of right now.
        self.ops = dict()
        """A dictionary mapping channels to a list of their operators."""
        self.halfplus = dict()
        """A dictionary mapping channels to a list of their half-ops and ops."""

    # def push(self, *args, **kargs):
    #     asynchat.async_chat.push(self, *args, **kargs)

    def __write(self, args, text=None):
        # print '%r %r %r' % (self, args, text)
        try:
            if text is not None:
                # 510 because CR and LF count too, as nyuszika7h points out
                temp = (' '.join(args) + ' :' + text)[:510] + '\r\n'
            else:
                temp = ' '.join(args)[:510] + '\r\n'
            log_raw(temp)
            self.push(temp)
        except IndexError:
            print "INDEXERROR", text
            #pass

    def write(self, args, text=None):
        """
        Send a command to the server. In the simplest case, ``args`` is a list
        containing just the command you wish to send, and ``text`` the argument
        to that command {e.g. write(['JOIN'], '#channel')}
        
        More specifically, ``args`` will be joined together, separated by a
        space. If text is given, it will be added preceeded by a space and a 
        colon (' :').
        
        Newlines and carriage returns ('\\n' and '\\r') are removed before
        sending. Additionally, if the joined ``args`` and ``text`` are more than
        510 characters, any remaining characters will not be sent.
        """
        # This is a safe version of __write
        def safe(input):
            input = input.replace('\n', '')
            input = input.replace('\r', '')
            return input.encode('utf-8')
        try:
            args = [safe(arg) for arg in args]
            if text is not None:
                text = safe(text)
            self.__write(args, text)
        except Exception, e: pass

    def run(self, host, port=6667):
        self.initiate_connect(host, port)

    def initiate_connect(self, host, port):
        if self.verbose:
            message = 'Connecting to %s:%s...' % (host, port)
            print >> sys.stderr, message,
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connect((host, port))
        try: asyncore.loop()
        except KeyboardInterrupt:
            sys.exit()

    def handle_connect(self):
        if self.verbose:
            print >> sys.stderr, 'connected!'
        if self.password:
            self.write(('PASS', self.password))
        self.write(('NICK', self.nick))
        self.write(('USER', self.user, '+iw', self.nick), self.name)

    def handle_close(self):
        self.close()
        print >> sys.stderr, 'Closed!'

    def collect_incoming_data(self, data):
        if data:
            log_raw(data)
            if hasattr(self, "logchan_pm") and self.logchan_pm and "PRIVMSG" in data and "#" not in data.split()[2]:
                self.msg(self.logchan_pm, data)
        self.buffer += data

    def found_terminator(self):
        line = self.buffer
        if line.endswith('\r'):
            line = line[:-1]
        self.buffer = ''

        #Adding a way to get a raw line for .whois
        self.raw = line


        # print line
        if line.startswith(':'):
            source, line = line[1:].split(' ', 1)
        else: source = None

        if ' :' in line:
            argstr, text = line.split(' :', 1)
        else: argstr, text = line, ''
        args = argstr.split()

        origin = Origin(self, source, args)
        self.dispatch(origin, tuple([text] + args))

        if args[0] == 'PING':
            self.write(('PONG', text))

    def dispatch(self, origin, args):
        pass

    def msg(self, recipient, text):
        self.sending.acquire()

        # Cf. http://swhack.com/logs/2006-03-01#T19-43-25
        if isinstance(text, unicode):
            try: text = text.encode('utf-8')
            except UnicodeEncodeError, e:
                text = e.__class__ + ': ' + str(e)
        if isinstance(recipient, unicode):
            try: recipient = recipient.encode('utf-8')
            except UnicodeEncodeError, e:
                return

        # No messages within the last 3 seconds? Go ahead!
        # Otherwise, wait so it's been at least 0.8 seconds + penalty
        if self.stack:
            elapsed = time.time() - self.stack[-1][0]
            if elapsed < 3:
                penalty = float(max(0, len(text) - 50)) / 70
                wait = 0.8 + penalty
                if elapsed < wait:
                    time.sleep(wait - elapsed)

        # Loop detection
        messages = [m[1] for m in self.stack[-8:]]
        if messages.count(text) >= 5:
            text = '...'
            if messages.count('...') >= 3:
                self.sending.release()
                return

        def safe(input):
            input = input.replace('\n', '')
            return input.replace('\r', '')
        self.__write(('PRIVMSG', safe(recipient)), safe(text))
        self.stack.append((time.time(), text))
        self.stack = self.stack[-10:]

        self.sending.release()
            
    def notice(self, dest, text):
        self.write(('NOTICE', dest), text)

    def error(self, origin, trigger):
        try:
            trace = traceback.format_exc()
            try:
                print trace
            except:
                pass
            logfile = open('logs/exceptions.log', 'a') #todo: make not hardcoded
            logfile.write('from %s at %s:\n' % (origin.sender, str(datetime.now())))
            logfile.write('Message was: <%s> %s\n' % (trigger.nick, trigger.group(0)))
            logfile.write(trace)
            logfile.write('----------------------------------------\n\n')
            logfile.close()
            lines = list(reversed(trace.splitlines()))

            report = [lines[0].strip()]
            for line in lines:
                line = line.strip()
                if line.startswith('File "/'):
                    report.append(line[0].lower() + line[1:])
                    break
            else: report.append('source unknown')

            self.msg(origin.sender, report[0] + ' (' + report[1] + ')')
        except Exception as e:
            self.msg(origin.sender, "Got an error.")
            self.debug("[core: error reporting]", "(From: "+origin.sender+") "+str(e), 'always')

    def handle_error(self):
        ''' Handle any uncaptured error in the core. Overrides asyncore's handle_error '''
        trace = traceback.format_exc()
        try:
            print trace
        except:
            pass
        self.debug("[core]", 'Fatal error in core, please review exception log', 'always')
        logfile = open('logs/exceptions.log', 'a') #todo: make not hardcoded
        logfile.write('Fatal error in core, handle_error() was called')
        logfile.write(trace)
        logfile.write('----------------------------------------\n\n')
        logfile.close()

    #Helper functions to maintain the oper list.
    def addOp(self, channel, name):
        self.ops[channel].add(name.lower())
    def addHalfOp(self, channel, name):
        self.halfplus[channel].add(name.lower())
    def delOp(self, channel, name):
        self.ops[channel].discard(name.lower())
    def delHalfOp(self, channel, name):
        self.halfplus[channel].discard(name.lower())
    def flushOps(self, channel):
        self.ops[channel] = set()
        self.halfplus[channel] = set()
    def startOpsList(self, channel):
        if not channel in self.halfplus: self.halfplus[channel] = set()
        if not channel in self.ops: self.ops[channel] = set()
        

class TestBot(Bot):
    def f_ping(self, origin, match, args):
        delay = m.group(1)
        if delay is not None:
            import time
            time.sleep(int(delay))
            self.msg(origin.sender, 'pong (%s)' % delay)
        else: self.msg(origin.sender, 'pong')
    f_ping.rule = r'^\.ping(?:[ \t]+(\d+))?$'

def main():
    # bot = TestBot('testbot', ['#d8uv.com'])
    # bot.run('irc.freenode.net')
    print __doc__

if __name__=="__main__":
    main()
