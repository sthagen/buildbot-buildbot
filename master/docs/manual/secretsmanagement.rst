
.. _secretManagement:

=================
Secret Management
=================

Requirements
============

Buildbot steps might need secrets to execute their actions. Secrets are used to execute commands or
to create authenticated network connections. Secrets may be a SSH key, a password, or a file
content like a wgetrc file or a public SSH key. To preserve confidentiality, the secret values must
not be printed or logged in the twisted or step logs. Secrets must not be stored in the Buildbot
configuration (master.cfg), as the source code is usually shared in SCM like git.

How to use Buildbot Secret Management
=====================================

Secrets and providers
---------------------

Buildbot implements several providers for secrets retrieval:

- File system based: secrets are written in a file.
  This is a simple solution for example when secrets are managed by a config management system like Ansible Vault.

- Third party backend based: secrets are stored by a specialized software.
  These solutions are usually more secure.

Secrets providers are configured if needed in the master configuration. Multiple providers can be
configured at once. The secret manager is a Buildbot service. The secret manager returns the
specific provider results related to the providers registered in the configuration.

How to use secrets in Buildbot
------------------------------

Secret can be used in Buildbot via the :class:`~IRenderable` mechanism.
Two :class:`~IRenderable` actually implement secrets.
:ref:`Interpolate` can be used if you need to mix secrets and other interpolation in the same argument.
:ref:`Secret` can be used if your secret is directly used as a component argument.

.. _Secret:

Secret
``````
    :ref:`Secret` is a simple renderable which directly renders a secret.

    .. code-block:: python

        Secret("secretName")

As argument to steps
````````````````````
The following example shows a basic usage of secrets in Buildbot.

.. code-block:: python

    from buildbot.plugins import secrets, util
    # First we declare that the secrets are stored in a directory of the filesystem
    # each file contains one secret identified by the filename
    c['secretsProviders'] = [secrets.SecretInAFile(dirname="/path/toSecretsFiles")]

    # then in a buildfactory:

    # use a secret on a shell command via Interpolate
    f1.addStep(ShellCommand(
        util.Interpolate("wget -u user -p '%(secret:userpassword)s' '%(prop:urltofetch)s'")))
    # .. or non shell form:
    f1.addStep(ShellCommand(["wget", "-u", "user", "-p",
                             util.Secret("userpassword"),
                             util.Interpolate("%(prop:urltofetch)s")]))

Secrets are also interpolated in the build like properties are.
Their values will be used in a command line for example.

As argument to services
```````````````````````

You can use secrets to configure services.
All services arguments are not compatible with secrets.
See their individual documentation for details.

.. code-block:: python

    # First we declare that the secrets are stored in a directory of the filesystem
    # each file contains one secret identified by the filename
    c['secretsProviders'] = [secrets.SecretInAFile(dirname="/path/toSecretsFiles")]

    # then for a reporter:
    c['services'] = [GitHubStatusPush(token=util.Secret("githubToken"))]

Secrets storages
----------------

.. _SecretInAFile:

SecretInAFile
`````````````

.. code-block:: python

    c['secretsProviders'] = [secrets.SecretInAFile(dirname="/path/toSecretsFiles")]

In the passed directory, every file contains a secret identified by the filename.

e.g: a file ``user`` contains the text ``pa$$w0rd``.

Arguments:

``dirname``
  (required) Absolute path to directory containing the files with a secret.

``strip``
  (optional) if ``True`` (the default), trailing newlines are removed from the file contents.

.. _HashiCorpVaultKvSecretProvider:

HashiCorpVaultKvSecretProvider
``````````````````````````````

.. code-block:: python

    c['secretsProviders'] = [
        secrets.HashiCorpVaultKvSecretProvider(
            authenticator=secrets.VaultAuthenticatorApprole(roleId="<role-guid>",
                                                            secretId="<secret-guid>"),
            vault_server="http://localhost:8200",
            secrets_mount="kv")
    ]

HashiCorpVaultKvSecretProvider allows to use HashiCorp Vault KV secret engine as secret provider.
Other secret engines are not supported by this particular provider.
For more information about Vault please visit: _`Vault`: https://www.vaultproject.io/

In order to use this secret provider, optional dependency ``hvac`` needs to be installed (``pip
install hvac``).

It supports different authentication methods with ability to re-authenticate when authentication
token expires (not possible using ``HvacAuthenticatorToken``).

Parameters accepted by ``HashiCorpVaultKvSecretProvider``:

 - ``authenticator``: required parameter, specifies Vault authentication method.
   Possible authenticators are:

    - ``VaultAuthenticatorToken(token)``: simplest authentication by directly providing the authentication token.
      This method cannot benefit from re-authentication mechanism and when token expires, secret
      provider will just stop working.

    - ``VaultAuthenticatorApprole(roleId, secretId)``: approle authentication using roleId and secretId.
      This is common method for automation tools fetching secrets from vault.

 - ``vault_server``: required parameter, specifies URL of vault server.

 - ``secrets_mount``: specifies mount point of KV secret engine in vault, default value is "secret".

 - ``api_version``: version of vault KV secret engine.
   Supported versions are 1 and 2, default value is 2.

 - ``path_delimiter``: character used to separate path and key name in secret identifiers.
   Default value is "|".

 - ``path_escape``: escape character used in secret identifiers to allow escaping of
   ``path_delimiter`` character in path or key values.
   Default value is "\".

The secret identifiers that need to be passed to, e.g. :ref:`Interpolate`, have format:
``"path/to/secret:key"``. In case path or key name does contain colon character, it is possible to
escape it using "\" or specify different separator character using ``path_delimiter`` parameter
when initializing secret provider.

Example use:

.. code-block:: python

    passwd = util.Secret('path/to/secret:password')

.. _SecretInPass:

SecretInPass
`````````````

.. code-block:: python

    c['secretsProviders'] = [secrets.SecretInPass(
                            gpgPassphrase="passphrase",
                            dirname="/path/to/password/store"
    )]

Passwords can be stored in a unix password store, encrypted using GPG keys. Buildbot can query
secrets via the ``pass`` binary found in the PATH of each worker. While ``pass`` allows for
multiline entries, the secret must be on the first line of each entry. The only caveat is that all
passwords Buildbot needs to access have to be encrypted using the same GPG key.

For more information about ``pass``, please visit _`pass`: https://www.passwordstore.org/

Arguments:

``gpgPassphrase``
  (optional) Pass phrase to the GPG decryption key, if any

``dirname``
  (optional) Absolute path to the password store directory, defaults to ~/.password-store

How to populate secrets in a build
----------------------------------

To populate secrets in files during a build, 2 steps are used to create and delete the files on the worker.
The files will be automatically deleted at the end of the build.

.. code-block:: python

        f = BuildFactory()
        with f.withSecrets(secrets_list):
            f.addStep(step_definition)

 or

.. code-block:: python

        f = BuildFactory()
        f.addSteps([list_of_step_definitions], withSecrets=secrets_list)

In both cases the secrets_list is a list of (secret path, secret value) tuples.

.. code-block:: python

        secrets_list = [('/first/path', Interpolate('write something and %(secret:somethingmore)s')),
                        ('/second/path', Interpolate('%(secret:othersecret)s'))]

The Interpolate class is used to render the value during the build execution.

How to configure a Vault instance
---------------------------------

Vault being a very generic system, it can be complex to install for the first time.
Here is a simple tutorial to install the minimal Vault to use with Buildbot.

Use Docker to install Vault
```````````````````````````

A Docker image is available to help users installing Vault.
Without any arguments, the command launches a Docker Vault developer instance, easy to use and test the functions.
The developer version is already initialized and unsealed.
To launch a Vault server please refer to the VaultDocker_ documentation:

.. _vaultDocker: https://hub.docker.com/_/vault/

In a shell:

.. code-block:: shell

    docker run vault

Starting the vault instance
```````````````````````````

Once the Docker image is created, launch a shell terminal on the Docker image:

.. code-block:: shell

      docker exec -i -t ``docker_vault_image_name`` /bin/sh

Then, export the environment variable VAULT_ADDR needed to init Vault.

.. code-block:: shell

      export VAULT_ADDR='vault.server.adress'

Writing secrets
```````````````

By default the official docker instance of Vault is initialized with a mount path of 'secret', a KV
v1 secret engine, and a second KV engine (v2) at 'secret/data'. Currently, Buildbot is "hard wired"
to expect KV v2 engines to reside within this "data" sub path. Provision is made to set a top level
path via the "secretsmount" argument: defaults to "secret". To add a new secret:

.. code-block:: shell

      vault kv put secret/new_secret_key value=new_secret_value
