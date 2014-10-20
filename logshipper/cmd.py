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

import argparse
import logging

import eventlet

import logshipper.pipeline

ARGS = None
LOG = None


def main():
    global LOG, ARGS
    parser = argparse.ArgumentParser(
        description="Detects resources related to a specific host or resource")

    parser.add_argument('--pipeline-path',
                        default="/etc/logshipper/pipelines/",
                        help='Where to find pipelines (*.yml files)')

    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--debug', action='store_true')

    ARGS = parser.parse_args()

    if ARGS.debug:
        log_level = 'DEBUG'
    elif ARGS.verbose:
        log_level = 'INFO'
    else:
        log_level = 'WARNING'

    logging.basicConfig(level=log_level)
    LOG = logging.getLogger(__name__)

    pipeline_manager = logshipper.pipeline.PipelineManager(ARGS.pipeline_path)

    pipeline_manager.start()

    try:
        while True:
            eventlet.sleep(86400)
    finally:
        pipeline_manager.stop()
