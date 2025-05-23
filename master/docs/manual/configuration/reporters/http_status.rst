.. bb:reporter:: HttpStatusPush

HttpStatusPush
++++++++++++++

.. py:currentmodule:: buildbot.reporters

.. code-block:: python

    from buildbot.plugins import reporters
    sp = reporters.HttpStatusPush(serverUrl="http://example.com/submit")
    c['services'].append(sp)

:class:`HttpStatusPush` sends HTTP POST requests to ``serverUrl``.
The body of request contains json-encoded data of the build as returned by the data API.
It is useful to create a status front end outside of Buildbot for better scalability.

.. note::

   The json data object sent is completely different from the one that was generated by 0.8.x buildbot.
   It is indeed generated using data api.

.. py:class:: HttpStatusPush(serverUrl, auth=None, headers=None, generators=None, debug=None, verify=None, cert=None, skip_encoding=False)

    :param string serverUrl: The url where to do the HTTP POST request
    :param auth: The authentication method to use.
        Refer to the documentation of the requests library for more information.
    :param dict headers: Pass custom headers to HTTP request.
    :type generators: list of IReportGenerator instances
    :param generators: A list of report generators that will be used to generate reports to be sent by this reporter.
        Currently the reporter will consider only the report generated by the first generator.
    :param boolean debug: Logs every requests and their response
    :param boolean verify: Disable ssl verification for the case you use temporary self signed certificates or path
        to a CA_BUNDLE file or directory. Refer to the documentation of the requests library for more information.
    :param string cert: Path to client side certificate, as a single file (containing the private key and certificate)
        or a tuple of both files’ paths. Refer to the documentation of the requests library for more information.
    :param boolean skip_encoding: Disables encoding of json data to bytes before pushing to server

Json object spec
~~~~~~~~~~~~~~~~

The default json object sent is a build object augmented with some more data as follow.

.. code-block:: json

    {
        "url": "http://yourbot/path/to/build",
        "<build data api values>": "[...]",
        "buildset": "<buildset data api values>",
        "builder": "<builder data api values>",
        "buildrequest": "<buildrequest data api values>"
    }


If you want another format, don't hesitate to use the ``format_fn`` parameter to customize the payload.
The ``build`` parameter given to that function is of type :bb:rtype:`build`, optionally enhanced with properties, steps, and logs information.
