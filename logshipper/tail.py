# Copyright 2014 Koert van der Veer
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


import glob
import logging

import eventlet
from eventlet.green import os
import eventlet.tpool
import pyinotify
import six

import logshipper.pyinotify_eventlet_notifier
import logshipper.input

LOG = logging.getLogger(__name__)

INOTIFY_FILE_MASK = pyinotify.IN_MODIFY | pyinotify.IN_OPEN
INOTIFY_DIR_MASK = (pyinotify.IN_CREATE | pyinotify.IN_DELETE |
                    pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVED_TO)


class Tail(logshipper.input.BaseInput):
    class FileTail:
        __slots__ = []
        fd = None
        path = None
        buffer = ""
        stat = None
        rescan = True

    def __init__(self, filename):
        if isinstance(filename, six.string_types):
            filename = [filename]

        self.globs = [os.path.abspath(f) for f in filename]
        self.watch_manager = pyinotify.WatchManager()
        self.tails = {}
        self.dir_watches = {}

        self.notifier = logshipper.pyinotify_eventlet_notifier.Notifier(
            self.watch_manager)

    def _inotify_file(self, event):
        tail = self.tails.get(event.path)
        if tail:
            if event.mask & pyinotify.IN_MODIFY:
                if tail.rescan:
                    self.process_tail(event.path)
                else:
                    self.read_tail(tail)
            else:
                tail.rescan = True

    def _inotify_dir(self, event):
        tail = self.tails.get(event.path)
        if tail:
            self.process_tail(event.path)

        if not event.dir:
            self.update_tails(self.globs)

    def _run(self):
        self.update_tails(self.globs, do_read_all=False)
        try:
            while self.should_run:
                self.notifier.loop(lambda _: not self.should_run)
        finally:
            self.update_tails([])

    def read_tail(self, tail):
        while True:
            buff = os.read(tail.fd, 1024)
            if not buff:
                return

            # Append to last buffer
            if tail.buffer:
                buff = tail.buff + buff
                tail.buff = ""

            lines = buff.splitlines(True)
            if lines[-1][-1] != "\n":  # incomplete line in buffer
                tail.buffer = lines[-1][-1]
                lines = lines[:-1]

            for line in lines:
                self.handler({'message': line[:-1]})

    def process_tail(self, path, should_seek=False):
        file_stat = os.stat(path)

        LOG.debug("process_tail for %s", path)
        # Find or create a tail.
        tail = self.tails.get(path)
        if tail:
            fd_stat = os.fstat(tail.fd)
            if fd_stat.st_size > os.lseek(tail.fd, 0, os.SEEK_CUR):
                LOG.debug("Something to read")
                self.read_tail(tail)
            if (tail.stat.st_size > file_stat.st_size or
                    tail.stat.st_ino != file_stat.st_ino):
                LOG.info("%s looks rotated. reopening", path)
                self.close_tail(tail)
                tail = None
                should_seek = False

        if not tail:
            LOG.info("Tailing %s", path)
            self.tails[path] = tail = self.open_tail(path, should_seek)
            tail.stat = file_stat
            self.read_tail(tail)

        tail.rescan = False

    def update_tails(self, globs, do_read_all=True):
        watches = set()

        for fileglob in globs:
            for path in glob.iglob(fileglob):
                self.process_tail(path, not do_read_all)
                watches.add(path)

        for vanished in (set(self.tails) - watches):
            LOG.info("%s vanished. Stop tailing", vanished)
            self.close_tail(self.tails.pop(vanished))

        for path in globs:
            while len(path) > 1:
                path = os.path.dirname(path)
                if path in self.dir_watches:
                    continue

                LOG.debug("Monitoring dir %s", path)

                self.dir_watches[path] = self.watch_manager.add_watch(
                    path, INOTIFY_DIR_MASK, do_glob=True,
                    proc_fun=self._inotify_dir)

                if '*' not in path and '?' not in path:
                    break

    def open_tail(self, path, go_to_end=False):
        tail = Tail.FileTail()
        tail.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        tail.path = path

        if go_to_end:
            os.lseek(tail.fd, 0, os.SEEK_END)

        wd = self.watch_manager.add_watch(
            path, INOTIFY_FILE_MASK,
            proc_fun=self._inotify_file)

        tail.wd = wd.pop(path)
        return tail

    def close_tail(self, tail):
        self.watch_manager.rm_watch(tail.wd)
        os.close(tail.fd)
        if tail.buffer:
            self.handler({'message': tail.buffer})
