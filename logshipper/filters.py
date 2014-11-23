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


import re
import time

import six

import logshipper.context

SKIP_STEP = 1
DROP_MESSAGE = 2
TRUTH_VALUES = set(['1', 'true', 'yes', 'on'])

PHASE_MATCH = 10
PHASE_MANIPULATE = 20
PHASE_FORWARD = 30
PHASE_DROP = 40


def prepare_match(parameters):
    """Matches regexes against message fields

    The match action matches a regex to a specific field of a message. If the
    regex doesn't match, the step is skipped. By default the ``message`` field
    will be matched against the regex, but by providing a dictionary of regexes
    you can select a different field, or multiple fields. If you're matching
    against multiple fields, all regexes need to match for the step to be
    executed.

    Named groups in regular expressions get registered as additional fields
    on the message. When matching against a single field, unnamed groups get
    registered as backreferences, which can be used throughout the rest of the
    step.

    Everywhere where single regexes can be used, a list of regexes can be used
    instead. The first match will be used in that case.

    Example:


    .. code:: yaml

        match:
            message: (Time):\s+(?P<time>\d+)
        set:
            part: "{1} {time}"

        # In: {"message": "The Time: 1234"}
        # Out: {"message": "The Time: 1234",
        #       "part": "Time 1234",       -- from the set commend
        #       "time": "1234"}            -- from the named group in the regex

    A shorter syntax is available when there's a single match against the
    ``message`` field, the above example is equivalent to:

    .. code:: yaml

        match: (start_time):\s+(?P<time>\d+)
        set:
            part: "{1} {time}"
    """
    if not isinstance(parameters, dict):
        parameters = {"message": parameters}

    regexes = [
        (a, [re.compile(b)]
            if isinstance(b, six.string_types)
            else [re.compile(c) for c in b])
        for (a, b) in parameters.items()]

    def handle_match(message, context):
        matches = {}
        last_match = None
        last_match_key = None
        for field_name, regex_list in regexes:
            field_data = message.get(field_name)
            for regex in regex_list:
                m = regex.search(field_data)
                if m:
                    break
            else:
                return SKIP_STEP

            matches[field_name] = last_match = m
            last_match_key = field_name

        for m in matches.values():
            message.update(m.groupdict())

        context.matches = matches

        if len(matches) == 1:

            context.match_field = last_match_key
            context.match = last_match
            context.backreferences = [context.match.group(0)]
            context.backreferences.extend(context.match.groups())

    handle_match.phase = PHASE_MATCH
    return handle_match


def prepare_extract(parameters):
    """Matches regexes against message fields and extracts any matches

    Equivalent to a match followed by an empty replace. This is especially
    useful when named groups are used.
    """

    matcher = prepare_match(parameters)

    def handle_extract(message, context):
        result = matcher(message, context)
        if result != SKIP_STEP:
            for field_name, match in context.matches.items():
                base = message[field_name]
                message[field_name] = "".join((base[:match.start()],
                                               base[match.end():]))
        return result

    handle_extract.phase = PHASE_MATCH
    return handle_extract


def prepare_edge(parameters):
    """Watches an expresion for changes

    This filter matches when the trigger changes.

    ```trigger```
        The field to match on. Typically contains backreferences or variable
        substitutions.
    ```backlog```
        The number of historic values to match against. The backlog eviction
        algorithm is LRU.
    """

    if isinstance(parameters, six.string_types):
        parameters = {"trigger": parameters}

    trigger = logshipper.context.prepare_template(parameters['trigger'])
    queue = dict()
    backlog = int(parameters.get('backlog', 1))

    def handle_edge(message, context):
        value = trigger.interpolate(context)
        if value in queue:
            queue[value] = time.time()
            return SKIP_STEP

        if len(queue) >= backlog:
            items = sorted((t, k) for (k, t) in queue.items())
            queue.pop(items[0][1])

        queue[value] = time.time()

    return handle_edge


def prepare_replace(parameters):
    """Replaces the matched strings with some text.

    Replace takes one parameter, the replacement text. The replacement text
    can be templated, in which case the textual backreferences are available
    as numbers in curly braces, e.g. ```{1}```). Note that backreferences are
    only available when the match acted on a single field.
    """

    template = logshipper.context.prepare_template(parameters or "")

    def handle_replace(message, context):
        base = message[context.match_field]
        for field_name, match in context.matches.items():
            base = message[field_name]
            message[field_name] = "".join((base[:match.start()],
                                           template.interpolate(context),
                                           base[match.end():]))

    return handle_replace


def prepare_set(parameters):
    """Sets fields of messages

    The ``set`` action allows you to set fields of messages. You can use it to
    add conditional flags, or combine it with them match action to perform
    message feature extraction.

    .. code:: yaml

        match: Foo=(\d+)
        set:
            foo: "{1}s"
            has_foo: True

        # In: {"message": "Foo=1234"}
        # Out: {"message": "Foo=1234",
        #       "foo": "1234s",
        #       "has_foo": true}
    """
    assert isinstance(parameters, dict)

    parameters = [(key, logshipper.context.prepare_template(value))
                  for (key, value) in parameters.items()]

    def handle_set(message, context):
        for fieldname, template in parameters:
            message[fieldname] = template.interpolate(context)

    return handle_set


def prepare_unset(parameters):
    """Unsets fields

    Example:

    .. code:: yaml

        unset:
        - foo
    """
    if isinstance(parameters, six.string_types):
        parameters = [p.strip() for p in parameters.split(",")]

    assert isinstance(parameters, list)

    def handle_unset(message, context):
        for field in parameters:
            message.pop(field, None)
    return handle_unset


def prepare_drop(parameters):
    """Drops messages

    Messages that encounter a drop action are dropped from the pipeline. If the
    message has been sent to other pipelines using the ``call`` action, the
    the message will not be dropped from those pipelines.

    Example:

    .. code:: yaml

        match: ^DEBUG
        drop:
    """
    handler = lambda message, parameters: DROP_MESSAGE
    handler.phase = PHASE_DROP
    return handler


def prepare_python(parameters):
    """Allows execution of python code

    The provided code is executed. The variable ``message`` is set to the
    a mapping type (dict) representing the message. Any changes to the message
    will be visible in the rest of the pipeline.

    The variable ``context`` is set to the context for message processing. The
    details of this object are version-dependent, and considered internal. Use
    at your own risk

    .. code:: yaml

        python: |
            message['msglen'] = len(message.get('message', ''))
    """

    code = compile(parameters, "pipeline", "exec")

    def handle_python(message, context):
        namespace = {
            'message': message,
            'context': 'context',
        }
        exec(code, namespace)

    handle_python.phase = PHASE_MANIPULATE + 5

    return handle_python
