import os
import logging
import tempfile
import uuid

import pytest

from nondjango.storages import utils, files, storages

logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)
logging.getLogger('s3transfer').setLevel(logging.ERROR)


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


# From: https://docs.minio.io/docs/aws-cli-with-minio.html
MINIO_S3_SETTINGS = {
    'AWS_ACCESS_KEY_ID': 'Q3AM3UQ867SPQQA43P2F',
    'AWS_SECRET_ACCESS_KEY': 'zuf+tfteSlswRu7BJ86wekitnifILbZam1KYY3TG',
    'AWS_S3_REGION_NAME': 'us-east-1',
    'AWS_S3_ENDPOINT_URL': 'https://play.min.io:9000',
    'AWS_S3_SIGNATURE_VERSION': 's3v4',
}


@pytest.mark.parametrize("storage_class, storage_params", [
    (storages.TemporaryFilesystemStorage, {}),
    (storages.S3Storage, {
        'settings': MINIO_S3_SETTINGS,
        'workdir': f's3://nondjango-storages-test/storage-test-{uuid.uuid4()}/'
    }),
])
def test_file_read_write(storage_class, storage_params):
    payload = 'test payload'
    storage = storage_class(**storage_params)
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
