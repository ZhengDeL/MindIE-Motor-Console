"""Explicit account and OIDC repository operations, shared by SQL dialects.

The query catalog is fixed source code. Callers supply values, never SQL. The
driver binds named parameters; no runtime SQL rewriting is used.
"""
from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError
from ..ports.storage import Records, DuplicateIdentity
from .schema import users


class _NamedRepository:
    def __init__(self, connection, *, write=False):
        self._connection = connection
        self._write = write

    def _query(self, statement, values):
        if not self._write and not statement.startswith('SELECT '):
            from ..ports.storage import StorageError
            raise StorageError('A write unit of work is required for this account operation')
        try:
            result = self._connection.execute(text(statement), values)
            return Records(result.mappings().all() if result.returns_rows else (), rowcount=result.rowcount)
        except IntegrityError as error:
            raise DuplicateIdentity('A unique identity or related record already exists') from error

class AccountRepository(_NamedRepository):
    def exists_any(self):
        return self._query('SELECT 1 FROM auth_users LIMIT 1', {})

    def remove_expired_throttles(self,cutoff):
        return self._query('DELETE FROM auth_throttle WHERE window_start<:p0', {'p0':cutoff})

    def get_throttle(self,bucket):
        return self._query('SELECT window_start,attempts FROM auth_throttle WHERE bucket=:p0', {'p0':bucket})

    def increment_throttle(self,bucket):
        return self._query('UPDATE auth_throttle SET attempts=attempts+1 WHERE bucket=:p0', {'p0':bucket})

    def reset_throttle(self,bucket,window_start):
        return self._query('INSERT INTO auth_throttle VALUES(:p0,:p1,1) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,attempts=1', {'p0':bucket,'p1':window_start})

    def prune_expired_sessions(self,now,idle_cutoff):
        return self._query('DELETE FROM auth_sessions WHERE expires_at<=:p0 OR last_seen<=:p1', {'p0':now,'p1':idle_cutoff})

    def create_session(self,token_hash,user_id,auth_version,created_at,last_seen,expires_at):
        return self._query('INSERT INTO auth_sessions VALUES(:p0,:p1,:p2,:p3,:p4,:p5)', {'p0':token_hash,'p1':user_id,'p2':auth_version,'p3':created_at,'p4':last_seen,'p5':expires_at})

    def trim_user_sessions(self,user_id):
        return self._query('DELETE FROM auth_sessions WHERE user_id=:user_id AND token_hash NOT IN (SELECT token_hash FROM auth_sessions WHERE user_id=:user_id ORDER BY created_at DESC,token_hash DESC LIMIT 10)', {'user_id':user_id})

    def get_session_account(self,token_hash):
        return self._query('SELECT u.*,s.token_hash,s.auth_version AS session_version,s.expires_at,s.last_seen FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=:p0', {'p0':token_hash})

    def touch_session(self,last_seen,token_hash):
        return self._query('UPDATE auth_sessions SET last_seen=:p0 WHERE token_hash=:p1', {'p0':last_seen,'p1':token_hash})

    def revoke_session(self,token_hash):
        return self._query('DELETE FROM auth_sessions WHERE token_hash=:p0', {'p0':token_hash})

    def create_user(self,user_id,username,display_name,password_hash,role,created_at,updated_at,auth_source):
        return self._query('INSERT INTO auth_users(id,username,display_name,password_hash,role,disabled,auth_version,created_at,updated_at,auth_source) VALUES(:p0,:p1,:p2,:p3,:p4,0,1,:p5,:p6,:p7)', {'p0':user_id,'p1':username,'p2':display_name,'p3':password_hash,'p4':role,'p5':created_at,'p6':updated_at,'p7':auth_source})

    def get_user(self,user_id):
        return self._query('SELECT * FROM auth_users WHERE id=:p0', {'p0':user_id})

    def get_username(self,username):
        return self._query('SELECT * FROM auth_users WHERE username=:p0', {'p0':username})

    def list_users(self):
        return self._query('SELECT * FROM auth_users ORDER BY username', {})

    def get_admin_status(self,user_id):
        return self._query('SELECT role,disabled FROM auth_users WHERE id=:p0', {'p0':user_id})

    def get_session_window(self,token_hash):
        return self._query('SELECT expires_at,last_seen FROM auth_sessions WHERE token_hash=:p0', {'p0':token_hash})

    def count_active_admins(self):
        return self._query("SELECT count(*) FROM auth_users WHERE role='admin' AND disabled=0", {})

    def count_active_local_admins(self):
        return self._query("SELECT count(*) FROM auth_users WHERE role='admin' AND disabled=0 AND auth_source='local'", {})

    def revoke_user_sessions(self,user_id):
        return self._query('DELETE FROM auth_sessions WHERE user_id=:p0', {'p0':user_id})

    def delete_identities(self,user_id):
        return self._query('DELETE FROM auth_identities WHERE user_id=:p0', {'p0':user_id})

    def delete_user(self,user_id):
        return self._query('DELETE FROM auth_users WHERE id=:p0', {'p0':user_id})

    def update_user(self,role,disabled,display_name,password_hash,updated_at,user_id):
        return self._query('UPDATE auth_users SET role=:p0,disabled=:p1,display_name=:p2,password_hash=:p3,auth_version=auth_version+1,updated_at=:p4 WHERE id=:p5', {'p0':role,'p1':disabled,'p2':display_name,'p3':password_hash,'p4':updated_at,'p5':user_id})

    def update_password(self,password_hash,updated_at,user_id):
        return self._query('UPDATE auth_users SET password_hash=:p0,auth_version=auth_version+1,updated_at=:p1 WHERE id=:p2', {'p0':password_hash,'p1':updated_at,'p2':user_id})

    def has_session(self,token_hash):
        return self._query('SELECT 1 FROM auth_sessions WHERE token_hash=:p0', {'p0':token_hash})

    def has_local_admin(self):
        return self._query("SELECT 1 FROM auth_users WHERE role='admin' AND disabled=0 AND auth_source='local' LIMIT 1", {})

    def get_federated_user(self,issuer,subject):
        return self._query('SELECT u.* FROM auth_identities i JOIN auth_users u ON u.id=i.user_id WHERE i.issuer=:p0 AND i.subject=:p1', {'p0':issuer,'p1':subject})

    def bind_identity(self,issuer,subject,user_id):
        return self._query('INSERT INTO auth_identities VALUES(:p0,:p1,:p2)', {'p0':issuer,'p1':subject,'p2':user_id})

    def set_role(self,role,updated_at,user_id):
        return self._query('UPDATE auth_users SET role=:p0,auth_version=auth_version+1,updated_at=:p1 WHERE id=:p2', {'p0':role,'p1':updated_at,'p2':user_id})

    def set_display(self,display_name,updated_at,user_id):
        return self._query('UPDATE auth_users SET display_name=:p0,updated_at=:p1 WHERE id=:p2', {'p0':display_name,'p1':updated_at,'p2':user_id})

    def public_names(self, identifiers):
        values = sorted(set(identifiers))
        if not values:
            return {}
        found = {}
        for offset in range(0, len(values), 500):
            result = self._connection.execute(select(users.c.id, users.c.username, users.c.display_name).where(users.c.id.in_(values[offset:offset+500])))
            for row in result.mappings():
                found[row['id']] = dict(row)
        return found


class OIDCRepository(_NamedRepository):
    def remove_expired(self,cutoff):
        return self._query('DELETE FROM auth_oidc_flows WHERE expires_at<:p0', {'p0':cutoff})

    def create(self,state_hash,browser_hash,verifier,nonce,created_at,expires_at):
        return self._query('INSERT INTO auth_oidc_flows VALUES(:p0,:p1,:p2,:p3,:p4,:p5,0)', {'p0':state_hash,'p1':browser_hash,'p2':verifier,'p3':nonce,'p4':created_at,'p5':expires_at})

    def consume(self,state_hash):
        return self._query("UPDATE auth_oidc_flows SET consumed=1,verifier='',nonce='' WHERE state_hash=:p0 AND consumed=0", {'p0':state_hash})

    def get(self,state_hash):
        return self._query('SELECT * FROM auth_oidc_flows WHERE state_hash=:p0', {'p0':state_hash})
