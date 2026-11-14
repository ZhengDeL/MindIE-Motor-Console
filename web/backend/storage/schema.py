"""Portable schema. Changes run only while the plugin's writer lease is held."""
from sqlalchemy import (MetaData, Table, Column, Text, Integer, Float, ForeignKey,
                        CheckConstraint, Index, BigInteger)

APPLICATION_ID = 0x4D43534C
SCHEMA_VERSION = 2
metadata = MetaData()

resources = Table('resources', metadata,
    Column('kind', Text, primary_key=True), Column('id', Text, primary_key=True),
    Column('body', Text, nullable=False), Column('created_order', BigInteger, nullable=False, server_default='0'))
Index('resources_order', resources.c.kind, resources.c.created_order)
revisions = Table('revisions', metadata,
    Column('id', Text, primary_key=True), Column('service_id', Text, nullable=False),
    Column('created_at', Text, nullable=False), Column('body', Text, nullable=False))
Index('revisions_service', revisions.c.service_id)
audit = Table('audit', metadata,
    Column('id', Text, primary_key=True), Column('time', Text, nullable=False),
    Column('body', Text, nullable=False), Column('actor', Text, nullable=False, server_default=''),
    Column('request_id', Text, nullable=False, server_default=''))
for name in ('time','actor','request_id'):
    Index('audit_'+name, audit.c[name])
migrations = Table('schema_migrations', metadata,
    Column('version', Integer, primary_key=True), Column('from_version', Integer, nullable=False),
    Column('applied_at', Text, nullable=False), Column('backup_manifest', Text))
platform_metadata = Table('platform_metadata', metadata,
    Column('key', Text, primary_key=True), Column('value', Text, nullable=False))
users = Table('auth_users', metadata,
    Column('id', Text, primary_key=True), Column('username', Text, nullable=False, unique=True),
    Column('display_name', Text, nullable=False), Column('password_hash', Text),
    Column('role', Text, nullable=False), Column('disabled', Integer, nullable=False, server_default='0'),
    Column('auth_version', Integer, nullable=False, server_default='1'),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False),
    Column('auth_source', Text, nullable=False, server_default='local'),
    CheckConstraint("role IN ('admin','operator','viewer')", name='auth_users_role'))
sessions = Table('auth_sessions', metadata,
    Column('token_hash', Text, primary_key=True), Column('user_id', Text, ForeignKey('auth_users.id'), nullable=False),
    Column('auth_version', Integer, nullable=False), Column('created_at', Float, nullable=False),
    Column('last_seen', Float, nullable=False), Column('expires_at', Float, nullable=False))
Index('auth_sessions_user', sessions.c.user_id)
throttle = Table('auth_throttle', metadata,
    Column('bucket', Text, primary_key=True), Column('window_start', Float, nullable=False),
    Column('attempts', Integer, nullable=False))
identities = Table('auth_identities', metadata,
    Column('issuer', Text, primary_key=True), Column('subject', Text, primary_key=True),
    Column('user_id', Text, ForeignKey('auth_users.id'), nullable=False))
oidc_flows = Table('auth_oidc_flows', metadata,
    Column('state_hash', Text, primary_key=True), Column('browser_hash', Text, nullable=False),
    Column('verifier', Text, nullable=False), Column('nonce', Text, nullable=False),
    Column('created_at', Float, nullable=False), Column('expires_at', Float, nullable=False),
    Column('consumed', Integer, nullable=False, server_default='0'))
