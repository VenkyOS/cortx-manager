#!/usr/bin/env python3

"""
 ****************************************************************************
 Filename:          sessions.py
 Description:       Session management services

 Creation Date:     10/16/2019
 Author:            Oleg Babin
                    Alexander Nogikh
                    Artem Obruchnikov

 Do NOT modify or remove this copyright and confidentiality notice!
 Copyright (c) 2001 - $Date: 2015/01/14 $ Seagate Technology, LLC.
 The code contained herein is CONFIDENTIAL to Seagate Technology, LLC.
 Portions are also trade secret. Any use, duplication, derivation, distribution
 or disclosure of this code, for any reason, not expressly authorized is
 prohibited. All other rights are expressly reserved by Seagate Technology, LLC.
 ****************************************************************************
"""

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime, timedelta, timezone
from typing import Optional
from csm.common.log import Log
from csm.common.conf import Conf
from csm.core.blogic import const
from csm.eos.plugins.s3 import S3Plugin, IamConnectionConfig, IamError
# TODO: from csm.common.passwd import Passwd
from csm.core.data.models.users import UserType, User, Passwd
from csm.core.services.users import UserManager
from csm.common.errors import CsmError, CSM_ERR_INVALID_VALUE


class SessionCredentials:
    """ Base class for a variying part of the session
    depending on the user type (CSM, LDAP, S3).
    """

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id


class LocalCredentials(SessionCredentials):
    """ CSM local user specific session credentials - empty """

    def __init__(self, user_id: str) -> None:
        super().__init__(user_id)


class LdapCredentials(SessionCredentials):
    """ LDAP user specific session credentials - TBD """

    def __init__(self, user_id: str) -> None:
        super().__init__(user_id)


class S3Credentials(SessionCredentials):
    """ S3 account specific session credentials """

    def __init__(self, user_id: str, access_key: str,
                 secret_key: str, session_token: str) -> None:
        super().__init__(user_id)
        self._access_key = access_key
        self._secret_key = secret_key
        self._session_token = session_token

    @property
    def access_key(self):
        return self._access_key

    @property
    def secret_key(self):
        return self._secret_key

    @property
    def session_token(self):
        return self._session_token


class Session:
    """ Session data """

    Id = str

    def __init__(self, session_id: Id,
                 expiry_time: datetime,
                 credentials: SessionCredentials) -> None:
        self._session_id = session_id
        self._expiry_time = expiry_time
        self._credentials = credentials

    @property
    def session_id(self) -> Id:
        return self._session_id

    @property
    def expiry_time(self) -> datetime:
        return self._expiry_time

    @expiry_time.setter
    def expiry_time(self, expiry_time):
        self._expiry_time = expiry_time

    @property
    def credentials(self) -> SessionCredentials:
        return self._credentials


class SessionManager:
    """ Session management class """

    def __init__(self):
        self._stg = {}
        self._expiry_interval = timedelta(minutes=60)  # TODO: Load from config

    @property
    def expiry_interval(self):
        return self._expiry_interval

    def _generate_sid(self) -> Session.Id:
        return uuid.uuid4().hex

    def calc_expiry_time(self) -> datetime:
        now = datetime.now(timezone.utc)
        return now + self._expiry_interval

    async def create(self, credentials: SessionCredentials) -> Session:
        session_id = self._generate_sid()
        expiry_time = self.calc_expiry_time()
        session = Session(session_id, expiry_time, credentials)
        self._stg[session_id] = session
        return session

    async def delete(self, session_id: Session.Id) -> None:
        self._stg.pop(session_id)

    async def get(self, session_id: Session.Id) -> Optional[Session]:
        return self._stg.get(session_id, None)

    async def update(self, session: Session) -> None:
        self._stg[session.session_id] = session


class AuthPolicy(ABC):
    """ Base abstract class for various authentication policies """

    @abstractmethod
    async def authenticate(self, user: User, password: str) -> Optional[SessionCredentials]:
        ...


class LocalAuthPolicy(AuthPolicy):
    """ Local CSM user authentication policy """

    async def authenticate(self, user: User, password: str) -> Optional[SessionCredentials]:
        if Passwd.verify(password, user.password_hash):
            return LocalCredentials(user.user_id)
        return None


class LdapAuthPolicy(AuthPolicy):
    """ Customer LDAP user authentication policy """

    async def authenticate(self, user: User, password: str) -> Optional[SessionCredentials]:
        # ldap_session = LdapAuth(user.user_id, password)
        # if ldap_session:
        #    return LdapCredentials(user.user_id, ldap_session=ldap_session)
        return None


class S3AuthPolicy(AuthPolicy):
    """ S3 account authentication policy """

    async def authenticate(self, user: User, password: str) -> Optional[SessionCredentials]:
        cfg = IamConnectionConfig()
        cfg.host = Conf.get(const.CSM_GLOBAL_INDEX, 'S3.host')
        cfg.port = Conf.get(const.CSM_GLOBAL_INDEX, 'S3.port')
        cfg.max_retries_num = Conf.get(const.CSM_GLOBAL_INDEX, 'S3.max_retries_num')

        Log.debug(f'Authenticating {user.user_id}'
                  f' with S3 IAM server {cfg.host}:{cfg.port}')
        s3_conn_obj = S3Plugin()
        response = await s3_conn_obj.get_temp_credentials(user.user_id, password,
                                                          connection_config=cfg)
        if type(response) is not IamError:
            # return temporary credentials
            return S3Credentials(user_id=user.user_id,
                                access_key=response.access_key,
                                secret_key=response.secret_key,
                                session_token=response.session_token)

        Log.error(f'Failed to authenticate S3 account {user.user_id}')
        return None


class AuthService:
    """ Generic authentication service. Allows to use different
    authentication policies for different user types. """

    def __init__(self):
        self._policies = {
            UserType.CsmUser.value: LocalAuthPolicy(),
            UserType.LdapUser.value: LdapAuthPolicy(),
            UserType.S3AccountUser.value: S3AuthPolicy(),
        }

    async def authenticate(self, user: User, password: str) -> Optional[SessionCredentials]:
        policy = self._policies.get(user.user_type, None)
        if policy:
            return await policy.authenticate(user, password)
        Log.error(f'Invalid user type {user.user_type}')
        return None


class LoginService:
    """ Login service. Authenticates a user with authentication service
    and creates a new session on login. Deletes existing session on logout.
    Checks for existing valid session on every API call. """

    def __init__(self, auth_service: AuthService,
                 user_manager: UserManager,
                 session_manager: SessionManager):
        self._auth_service = auth_service
        self._user_manager = user_manager
        self._session_manager = session_manager

    async def login(self, user_id, password):
        Log.debug(f'Logging in user {user_id}')

        user = await self._user_manager.get(user_id)
        if not user:
            # TODO: Try to search Customer LDAP or S3 account
            # and create corresponding user record if found.
            Log.debug(f'User {user_id} does not exist in the local database - trying S3 account')
            user = User.instantiate_s3_account_user(user_id)

        credentials = await self._auth_service.authenticate(user, password)
        if credentials:
            session = await self._session_manager.create(credentials)
            if session:
                return session.session_id
            else:
                Log.critical(f'Failed to create a new session')
        else:
            Log.error(f'Failed to authenticate {user_id}')
        return None

    async def logout(self, session_id):
        Log.debug(f'Logging out session {session_id}')
        await self._session_manager.delete(session_id)

    async def auth_session(self, session_id: Session.Id) -> Session:
        session = await self._session_manager.get(session_id)
        if not session:
            raise CsmError(CSM_ERR_INVALID_VALUE, f'Invalid session id: {session_id}')

        # TODO: Check if user has not been dropped.
        # We can not do it for now as non-local S3
        # users are no present in the local user database.

        # Check Expiry Time
        if datetime.now(timezone.utc) > session.expiry_time:
            await self._session_manager.delete(session_id)
            raise CsmError(CSM_ERR_INVALID_VALUE, 'Session expired')

        # Refresh Expiry Time
        session.expiry_time = self._session_manager.calc_expiry_time()

        return session