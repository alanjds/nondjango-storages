import os
import logging
import tempfile
import boto3
import posixpath
import hashlib
from gzip import GzipFile
from botocore.exceptions import ClientError
from botocore.client import Config as botocore__Config
from io import BytesIO, StringIO, SEEK_END

from .utils import prepare_path, md5s3
from .files import File

logger = logging.getLogger(__name__)


class SuspiciousOperation(Exception):
    pass


class Settings(dict):
    "TODO: Implement something nicer!"
    pass


def force_text(base):
    return base.decode() if isinstance(base, bytes) else base


def safe_join(base, *paths):
    """
    A version of django.utils._os.safe_join for S3 paths.
    Joins one or more path components to the base path component
    intelligently. Returns a normalized version of the final path.
    The final path must be located inside of the base path component
    (otherwise a ValueError is raised).
    Paths outside the base path indicate a possible security
    sensitive operation.
    """
    starts_on_root = base.startswith('/')

    base_path = force_text(base)
    base_path = base_path.rstrip('/')
    paths = [force_text(p) for p in paths]

    final_path = base_path + '/'
    for path in paths:
        _final_path = posixpath.normpath(posixpath.join(final_path, path))
        # posixpath.normpath() strips the trailing /. Add it back.
        if path.endswith('/') or _final_path + '/' == final_path:
            _final_path += '/'
        final_path = _final_path
    if final_path == base_path:
        final_path += '/'

    # Ensure final_path starts with base_path and that the next character after
    # the base path is /.
    base_path_len = len(base_path)
    if (not final_path.startswith(base_path) or final_path[base_path_len] != '/'):
        raise ValueError('the joined path is located outside of the base path component')

    return final_path if starts_on_root else final_path.lstrip('/')


def _strip_prefix(text, prefix):
    return text[len(prefix):] if text.startswith(prefix) else text


def _strip_s3_path(path):
    assert path.startswith('s3://')
    bucket, _, path = _strip_prefix(path, 's3://').partition('/')
    return bucket, path


class BaseStorage:
    file_class = File

    def __init__(self, workdir=None, settings=None):
        self._settings = settings or Settings()
        self._workdir = workdir or os.getcwd()

    def _normalize_name(self, name):
        """
        Normalizes the name so that paths like /path/to/ignored/../something.txt
        work. We check to make sure that the path pointed to is not outside
        the directory specified by the LOCATION setting.
        """
        try:
            return safe_join(self._workdir, name)
        except ValueError:
            raise SuspiciousOperation(f"Attempted access to '{name}' denied.")

    def get_valid_name(self, name):
        """
        Return a filename, based on the provided filename, that's suitable for
        use in the target storage system.
        """
        walked_path = os.path.relpath(name) if name else ''
        if walked_path.startswith('../'):
            raise SuspiciousOperation(f"Attempted access to '{name}' denied.")
        return walked_path

    def read_into_stream(self, file_path, stream=None, mode='r'):
        raise NotImplementedError()

    def open(self, file_name, mode='r') -> File:
        """Retrieve the specified file from storage."""
        valid_name = self.get_valid_name(file_name)
        logger.debug('Opening %s', valid_name)
        return self.file_class(valid_name, storage=self, mode=mode)

    def _close(self, f):
        pass

    def delete(self, name):
        """
        Delete the specified file from the storage system.
        """
        raise NotImplementedError('subclasses of Storage must provide a delete() method')

    def _write(self, f, file_name):
        raise NotImplementedError()

    def listdir(self, path):
        """
        List the contents of the specified path. Return a 2-tuple of lists:
        the first item being directories, the second item being files.
        """
        raise NotImplementedError()

    def exists(self, name) -> bool:
        """
        Return True if a file referenced by the given name already exists in the
        storage system, or False if the name is available for a new file.
        """
        dirname, sep, filename = name.rpartition('/')
        dirnames, existing_files = self.listdir(dirname)
        if filename in existing_files:
            return True
        return False

    def size(self, name) -> int:
        """
        Returns the total size, in bytes, of the file referenced by name.
        For storage systems that aren’t able to return the file size
        this will raise NotImplementedError instead.
        """
        raise NotImplementedError()

    def _file_hash(self, file: File, function='') -> str:
        """
        Returns a hash of the file, possibly calculated by the storage in an
        optimized way. The hash is calculated with the well-known function name
        'function', e.g. 'md5' or 'sha256'. If 'function' is empty it is chosen
        by the storage.

        If the hash cannot be calculated, the storage should return None.
        """
        if file.storage != self:
            raise RuntimeError('Cannot answer hash of a File from another storage')
        return None


class S3Storage(BaseStorage):
    def __init__(self, settings=None, workdir='s3://s3storage/'):
        super(__class__, self).__init__(settings=settings)
        self._resource = None
        self._bucket_name, self._workdir = _strip_s3_path(workdir)
        self._workdir = os.path.relpath(self._workdir) if self._workdir else ''
        self._bucket  # Raises error on unavailable bucket

    @property
    def s3(self):
        logger.debug('Getting S3 resource')
        # See how boto resolve credentials in
        # http://boto3.readthedocs.io/en/latest/guide/configuration.html#guide-configuration
        if not self._resource:
            logger.debug('Resource does not exist, creating a new one...')
            resource_kwargs = dict(
                aws_access_key_id=self._settings.get('S3CONF_ACCESS_KEY_ID') or self._settings.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=self._settings.get('S3CONF_SECRET_ACCESS_KEY') or self._settings.get('AWS_SECRET_ACCESS_KEY'),
                aws_session_token=self._settings.get('S3CONF_SESSION_TOKEN') or self._settings.get('AWS_SESSION_TOKEN'),
                region_name=self._settings.get('S3CONF_S3_REGION_NAME') or self._settings.get('AWS_S3_REGION_NAME'),
                use_ssl=self._settings.get('S3CONF_S3_USE_SSL') or self._settings.get('AWS_S3_USE_SSL', True),
                endpoint_url=self._settings.get('S3CONF_S3_ENDPOINT_URL') or self._settings.get('AWS_S3_ENDPOINT_URL'),
            )  # yapf: disable

            signature_version = (self._settings.get('S3CONF_S3_SIGNATURE_VERSION')
                                 or self._settings.get('AWS_S3_SIGNATURE_VERSION'))
            pool_connections = self._settings.get('AWS_S3_MAX_POOL_CONNECTIONS')

            botoconfig = {}
            if signature_version:
                botoconfig['signature_version'] = signature_version
            if pool_connections:
                botoconfig['max_pool_connections'] = int(pool_connections)

            if botoconfig:
                resource_kwargs['config'] = botocore__Config(**botoconfig)
            self._resource = boto3.resource('s3', **resource_kwargs)
        return self._resource

    def read_into_stream(self, file_path, stream=None):
        file_name = self._normalize_name(self.get_valid_name(file_path))

        stream = stream or BytesIO()
        s3_file = self.s3.Object(self._bucket_name, file_name)

        try:
            s3_file.download_fileobj(stream)
            stream.seek(0)
            if s3_file.content_encoding == 'gzip':
                stream = GzipFile(mode=getattr(stream, 'mode', 'rb+'), fileobj=stream, mtime=0.0)
            return stream
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.debug('File %s in bucket %s does not exist', file_name, self._bucket_name)
                raise FileNotFoundError(f's3://{self._bucket_name}/{file_name}')
            else:
                raise

    @property
    def _bucket(self) -> 's3.Bucket':
        if hasattr(self, '__bucket'):
            return self.__bucket

        bucket = self.s3.Bucket(self._bucket_name)
        # Trigger existance check for this bucket
        # Let the errors bubble up if occur.
        bucket_exists = bucket.creation_date
        if not bucket_exists:
            if not self._settings.get('AWS_AUTO_CREATE_BUCKET', False):
                raise RuntimeError(f"Bucket '{self._bucket_name}' does not exists")

            try:
                self.s3.create_bucket(Bucket=self._bucket_name)
            except ClientError as e:
                if e.response['Error']['Code'] not in ['BucketAlreadyExists', 'BucketAlreadyOwnedByYou']:
                    raise

        self.__bucket = bucket
        return self.__bucket

    def _write(self, f, file_name):
        internal_name = self._normalize_name(file_name)
        logger.info('Writing to s3://%s/%s', self._bucket_name, internal_name)

        f.seek(0)
        content_md5 = hashlib.md5(f.read()).hexdigest()
        f.seek(0)
        content_sha256 = hashlib.sha256(f.read()).hexdigest()

        extra_args = {
            "Metadata": {
                'md5': content_md5,
                'sha256': content_sha256,
            },
        }

        if self._settings.get('AWS_IS_GZIPPED', False):
            f.seek(0, SEEK_END)
            extra_args['Metadata']['original_size'] = str(f.tell())

            f.seek(0)
            f = self._compress_content(f)
            extra_args['ContentEncoding'] = 'gzip'

        f.seek(0)
        self._bucket.upload_fileobj(f, internal_name, ExtraArgs=extra_args)

    def _compress_content(self, content: 'filelike') -> BytesIO:
        """
        Gzip a given bytes content.
        """
        content.seek(0)
        zbuf = BytesIO()
        #  The GZIP header has a modification time attribute (see http://www.zlib.org/rfc-gzip.html)
        #  This means each time a file is compressed it changes even if the other contents don't change
        #  For S3 this defeats detection of changes using MD5 sums on gzipped files
        #  Fixing the mtime at 0.0 at compression time avoids this problem
        zfile = GzipFile(mode='wb', fileobj=zbuf, mtime=0.0)
        try:
            zfile.write(content.read())
        finally:
            zfile.close()
        zbuf.seek(0)
        return zbuf

    def delete(self, name):
        internal_name = self.get_valid_name(name)
        # result = self._bucket.delete_objects(Delete={
        #     'Objects': [{'Key': internal_name}],
        # })
        s3_file = self.s3.Object(self._bucket_name, self._normalize_name(internal_name))
        result = s3_file.delete()
        if 'Errors' in result or result.get('DeleteMarker', True) != True:
            raise RuntimeError(f"Could not delete '{name}': {result}")
        return result

    def exists(self, name) -> bool:
        """
        Return True if a file referenced by the given name already exists in the
        storage system, or False if the name is available for a new file.
        """
        internal_name = self.get_valid_name(name)
        s3_file = self.s3.Object(self._bucket_name, self._normalize_name(internal_name))

        try:
            # See if the resource exists via a HEAD call to get the e-tag
            s3_file.e_tag
        except ClientError as err:
            err_msg = str(err)
            if '404' in err_msg and 'Not Found' in err_msg:
                return False
            raise
        return True

    def list(self, path):
        valid_name = self.get_valid_name(path)
        logger.debug('Listing %s', valid_name)
        bucket_name, path = _strip_s3_path(valid_name)
        bucket = self.s3.Bucket(bucket_name)
        try:
            for obj in bucket.objects.filter(Prefix=path):
                if not obj.key.endswith('/'):
                    yield obj.e_tag, _strip_prefix(obj.key, path)
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                logger.warning('Bucket does not exist, list() returning empty.')
            else:
                raise

    def listdir(self, name):
        valid_name = self.get_valid_name(name)
        path = self._normalize_name(valid_name)
        # The path needs to end with a slash, but if the root is empty, leave
        # it.
        if path and not path.endswith('/'):
            path += '/'

        directories = []
        files = []
        paginator = self.s3.meta.client.get_paginator('list_objects')
        pages = paginator.paginate(Bucket=self._bucket_name, Delimiter='/', Prefix=path)
        for page in pages:
            for entry in page.get('CommonPrefixes', ()):
                directories.append(posixpath.relpath(entry['Prefix'], path))
            for entry in page.get('Contents', ()):
                files.append(posixpath.relpath(entry['Key'], path))
        return directories, files

    def size(self, name: str) -> int:
        normalized_name = self._normalize_name(self.get_valid_name(name))
        s3_file = self.s3.Object(self._bucket_name, normalized_name)
        s3_file.load()  # Get metadata via HEAD
        if s3_file.content_encoding == 'gzip':
            return int(s3_file.metadata.get('original_size'.capitalize(), s3_file.content_length))
        return s3_file.content_length

    def _file_hash(self, file: File, function='') -> str:
        if file.storage != self:
            raise RuntimeError('Cannot answer hash of a File from another storage')

        internal_name = self.get_valid_name(file.name)
        s3_file = self.s3.Object(self._bucket_name, self._normalize_name(internal_name))
        try:
            # On S3 the E-Tag is the hash
            s3_file.e_tag
        except ClientError as err:
            err_msg = str(err)
            if '404' in err_msg and 'Not Found' in err_msg:
                return None
            raise

        if function:
            metadata = {k.lower(): v for k, v in s3_file.metadata.items()}
            return metadata.get(function, None)
        else:
            return s3_file.e_tag

    def url(self, name: str, check_for_inexistent=True) -> str:
        """
        Returns the URL where the contents of the file referenced by name can be accessed.
        This can raise NotImplementedError depending on the backend used.
        """
        if check_for_inexistent and not self.exists(name):
            return None

        internal_name = self.get_valid_name(name)
        url = self.s3.meta.client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': self._bucket_name,
                'Key': self._normalize_name(internal_name),
            },
            ExpiresIn=self._settings.get('URL_EXPIRATION', 3600),
            HttpMethod=None,
        )
        return url


class FilesystemStorage(BaseStorage):
    def _validate_path(self, path):
        return True

    def get_valid_name(self, name):
        valid_path = super(__class__, self).get_valid_name(name)
        return os.path.join(self._workdir, valid_path).replace('//', '/')

    def read_into_stream(self, file_name, stream=None, mode='r'):
        self._validate_path(file_name)
        if not stream:
            stream = BytesIO() if 'b' in mode else StringIO()
        with open(file_name, mode) as f:
            stream.write(f.read())
        stream.seek(0)
        return stream

    def _write(self, f, file_name):
        file_name = self._normalize_name(file_name)
        self._validate_path(file_name)
        prepare_path(file_name)
        open(file_name, 'wb').write(f.read())

    def delete(self, name):
        return os.unlink(name)

    def save(self, name, content):
        path = self._normalize_name(name)
        open(path, 'wb').write(content)

    def size(self, name):
        path = self._normalize_name(name)
        return os.path.getsize(path)

    def listdir(self, path):
        self._validate_path(path)
        path = self._normalize_name(path)

        for _, dirnames, filenames in os.walk(path):
            break
        else:
            dirnames, filenames = [], []
        return dirnames, filenames

    def list(self, path):
        self._validate_path(path)
        fixed_path = self._normalize_name(path)

        if os.path.isdir(fixed_path):
            for root, dirs, files in os.walk(fixed_path):
                for file in files:
                    yield md5s3(open(file, 'rb')), _strip_prefix(os.path.join(root, file), fixed_path)
        else:
            # only yields if it exists
            if os.path.exists(fixed_path):
                # the relative path of a file to itself is empty
                # same behavior as in boto3
                yield md5s3(open(fixed_path, 'rb')), ''

    def _file_hash(self, file: File, function='') -> str:
        function = function or 'md5'
        if file.storage != self:
            raise RuntimeError('Cannot answer hash of a File from another storage')

        if hasattr(hashlib, function):
            hasher = getattr(hashlib, function)
            with open(file.name, 'br') as opened:
                return hasher(opened.read()).hexdigest()
        else:
            return None


class TemporaryFilesystemStorage(FilesystemStorage):
    """
    Just a Django-less storage w/ partial Django Storage API implemented
    """
    def __init__(self):
        self._tempdir = None

    @property
    def _workdir(self):
        if not self._tempdir:
            self._tempdir = tempfile.TemporaryDirectory()
        return self._tempdir.name
