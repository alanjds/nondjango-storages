# nondjango.storages

[![Build Status](https://travis-ci.org/alanjds/nondjango-storages.svg?branch=master)](https://travis-ci.org/alanjds/nondjango-storages)
[![Join the chat at https://gitter.im/nondjango/storages](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/nondjango/storages?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)

This package provides implementations of
[Django' File Storage API](https://docs.djangoproject.com/en/3.0/ref/files/storage/#module-django.core.files.storage)
but without having Django as dependency. This is inspired on
[django-storages](https://pypi.org/project/django-storages/) package, that implements
several storages as Azure, Dropbox, SFTP and so, but depends on Django.

## Why nondjango?

Django is recognized as a "beast" web framework, with everything and a sink.
Is very good to have your needs covered. But when you want to fly light, Django
is waaay too much.

After using Django for some time you realize that several parts are not really
tied to Django and could be useful outside of it. Storage is one of this parts.

## Why a Django interface if not using Django itself?

Then you end up doing little scripts to fit your needs. And your co-workers do
the same. After that, a newcomer on the company (or to the project) will have to
learn how to interact with half-dozen of ways to upload a file,
depending on the project and backing storage choices like S3 or Azure or IPFS.

This makes maintenance harder and learning curve steeper.

Instead, stick with a well-known interface that have usage tutorials available
online and is generic enough to not tie you on a vendor or implementation.
Had you tried to exchange from SFTP to S3 on the past?
Ideally, it should be as easy as pointing the driver.


## Requirements & Compatibility

-  Python (3.5, 3.6, 3.7, 3.8)
-  `boto3` for S3 backend
-  `flit` for installation from sources

## Installation

You can install this library using pip:

```console
pip install nondjango-storages
```

Or via sources, using [Flit](https://pypi.org/project/flit/):

```console
git clone https://github.com/alanjds/nondjango-storages.git
cd nondjango-storages
pip install flit
flit install
```


## Quickstart

The interface loosely implements the Django's [Storage class interface](https://docs.djangoproject.com/en/3.0/ref/files/storage/#the-storage-class), described on `nondjango.storages.BaseStorage`. Right now there is implementations for
FilesystemStorage and S3Storage only,
aside from the TemporaryFilesystemStorage used on automated tests.

Instantiate the desired storage, `BaseStorage.open()` your filelike
and manipulate it. When done, remember to `.close()` it.
Closing the file is specially important on some storages, as S3.


### Initializing some storage

As the storages are not tied to a central settings file, more than one can be
instantiated at the same time.

```python
from nondjango.storages import S3Storage, TemporaryFilesystemStorage

# Initializing a local temporary folder storage:
disposable_storage = TemporaryFilesystemStorage()

# Initializing an S3Storage:
S3_SETTINGS = {
    'AWS_ACCESS_KEY_ID': 'Q3AM3UQ867SPQQA43P2F',
    'AWS_SECRET_ACCESS_KEY': 'zuf+tfteSlswRu7BJ86wekitnifILbZam1KYY3TG',
    'AWS_S3_REGION_NAME': 'us-east-1',
    'AWS_S3_ENDPOINT_URL': 'https://play.min.io:9000',
    'AWS_S3_SIGNATURE_VERSION': 's3v4',
}
s3_storage = S3Storage(settings=S3_SETTINGS, workdir='s3://nondjango-storages-test/storage-test-readme/')
```

### Opening and manipulating a file

Access files via `BaseStorage.open()` implementations,
following the [Django docs](https://docs.djangoproject.com/en/3.0/ref/files/storage/#django.core.files.storage.Storage.open) over its usage.

```python
with s3_storage.open('spam.txt', 'w') as file_on_cloud:
    file_on_cloud.write('Eggs')

with disposable_storage.open('spam.txt', 'w') as file_on_disk:
    file_on_disk.write('Eggs')
```

If you are not using files as context managers, **remember to close your files**:

```python
>>> file_on_cloud = s3_storage.open('span.txt', 'r')
>>> file_on_cloud.read()
'Eggs'
>>> file_on_cloud.close()
```

## Advanced

Most of the generally-useful interface of Django Filestorage API is implemented,
as BaseStorage `.size()`, `.url()`, `.listdir()`, `.exists()` and `.delete()`.
Also some extra tools like `.hash()`, that computes or grabs the file hashes if
available. For now, S3Storage keeps the MD5 and Sha256 of the files on upload.
Filesystem-backed storages computes them on the fly.

```python
>>> file_on_cloud = s3_storage.open('span.txt')
>>> file_on_cloud.hash('md5')
'9890f06976131702b942e59eda2f750a'
>>> file_on_cloud.hash('sha256')
'f1c1f57728f932efde20e53703ee5f96b1cebdc15b8578b7faa727c89dbfe03f'
```


## Testing

In order to get started with testing, you will need to install [tox](https://tox.readthedocs.io/en/latest/).
Once installed, you can then run one environment locally, to speed up your development cycle:

```
$ tox -e py37
```

Once you submit a pull request, your changes will be run against many environments with Travis CI.


## License

This package is licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
and can undestand more at http://choosealicense.com/licenses/apache/ on the
sidebar notes. Another copy is attached on the LICENSE file on the root of this
repo.

Apache Licence v2.0 is a MIT-like licence. This means, in plain English:
- It's truly open source
- You can use it as you wish, for money or not
- You can sublicense it (change the licence!!)
- This way, you can even use it on your closed-source project
As long as:
- You cannot use the authors name, logos, etc, to endorse a project
- You keep the authors copyright notices where this code got used, even on your closed-source project
(come on, even Microsoft kept BSD notices on Windows about its TCP/IP stack :P)

### API License?

After the Oracle vs Google claim, the copyright of APIs became a grey area.
Despite personal believes, consider that the reimplemented API is of Django and
Django is licensed as 3-Clause BSD. Such license is included on the
LICENSE-DJANGO file in the root folder.
This should be enough even if Oracle wins and come to buy Django somehow someday.
