from gamehost_shared.camel_model import CamelModel


class BackupRequestIn(CamelModel):
    volume_name: str
    s3_key: str


class BackupResultOut(CamelModel):
    size_bytes: int
