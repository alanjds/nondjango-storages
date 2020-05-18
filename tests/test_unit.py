import os
import logging
import tempfile
import uuid

import requests
import pytest

from nondjango.storages import utils, files, storages

logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)
logging.getLogger('s3transfer').setLevel(logging.ERROR)

# From: https://docs.minio.io/docs/aws-cli-with-minio.html
MINIO_S3_SETTINGS = {
    'AWS_ACCESS_KEY_ID': 'Q3AM3UQ867SPQQA43P2F',
    'AWS_SECRET_ACCESS_KEY': 'zuf+tfteSlswRu7BJ86wekitnifILbZam1KYY3TG',
    'AWS_S3_REGION_NAME': 'us-east-1',
    'AWS_S3_ENDPOINT_URL': 'https://play.min.io:9000',
    'AWS_S3_SIGNATURE_VERSION': 's3v4',
    'AWS_AUTO_CREATE_BUCKET': True,
}


@pytest.fixture(scope="module",
                params=[(storages.TemporaryFilesystemStorage, {}),
                        (storages.S3Storage, {
                            'settings': MINIO_S3_SETTINGS,
                            'workdir': f's3://nondjango-storages-test/storage-test-{uuid.uuid4()}/'
                        }),
                        (storages.S3Storage, {
                            'settings': {
                                **MINIO_S3_SETTINGS,
                                'AWS_S3_MAX_POOL_CONNECTIONS': 30,
                            },
                            'workdir': f's3://nondjango-storages-test/storage-test-{uuid.uuid4()}/'
                        }),
                        (storages.S3Storage, {
                            'settings': {
                                **MINIO_S3_SETTINGS,
                                'AWS_IS_GZIPPED': True,
                            },
                            'workdir': f's3://nondjango-storages-test/storage-test-{uuid.uuid4()}/'
                        })],
                ids=lambda x: x[0])
def storage(request):
    storage_class, init_params = request.param
    yield storage_class(**init_params)


def test_prepare_empty_path():
    utils.prepare_path('')


def test_filesystem_storages_honor_workdir():
    storage = storages.TemporaryFilesystemStorage()
    filename = 'test_file.txt'
    f = storage.open(filename, 'w+')
    f.write('test payload')
    f.close()

    workdir = storage._workdir
    assert filename in os.listdir(workdir), 'File is not on the storage workdir'


def test_file_read_write(storage):
    payload = 'test payload'
    try:
        storage.delete('test_file.txt')
    except NotImplementedError:
        raise
    except Exception:
        pass

    assert not storage.exists('test_file.txt')

    with storage.open('test_file.txt', 'w+') as f:
        f.write(payload)
    assert storage.exists('test_file.txt')

    with storage.open('test_file.txt', 'r') as f2:
        assert f2.read() == payload


def test_file_size(storage):
    payload = '123456789'
    with storage.open('test_file.txt', 'w+') as f:
        f.write(payload)
    assert storage.size('test_file.txt') == len(payload), 'Wrong size returned?'


def test_file_hash(storage):
    payload = '123456789'
    md5 = '25f9e794323b453885f5181f1b624d0b'
    sha256 = '15e2b0d3c33891ebb0f1ef609ec419420c20e320ce94c65fbc8c3312448eb225'
    with storage.open('test_file.txt', 'w+') as f:
        f.write(payload)

    f = storage.open('test_file.txt')
    assert f.hash('md5') == md5, 'Wrong MD5 hash?'
    assert f.hash('sha256') == sha256, 'Wrong sha256 hash?'
    assert f.hash() is not None, 'Storage without a default hash function?'


def test_file_url():
    storage = storages.S3Storage(settings=MINIO_S3_SETTINGS,
                                 workdir=f's3://nondjango-storages-test/storage-test-{uuid.uuid4()}/')
    payload = 'test payload'
    with storage.open('test_file.txt', 'w+') as f:
        f.write(payload)

    assert storage.url('inexistent_file', check_for_inexistent=False), 'Checking before producing the URL?'
    assert storage.url('inexistant_file') is None, 'Generating URLs for inexistent files?'

    test_file_url = storage.url('test_file.txt')
    assert test_file_url.startswith('https://play.min.io:9000/nondjango-storages-test/storage-test-')
    assert '/test_file.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256' in test_file_url
    assert requests.get(test_file_url).text == payload, 'Generating broken URLs?'
