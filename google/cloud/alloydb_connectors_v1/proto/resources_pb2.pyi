from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper

from google.api import field_behavior_pb2 as _field_behavior_pb2

DESCRIPTOR: _descriptor.FileDescriptor

class MetadataExchangeRequest(_message.Message):
    __slots__ = ["auth_type", "oauth2_token", "user_agent"]

    class AuthType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
    AUTH_TYPE_FIELD_NUMBER: _ClassVar[int]
    AUTH_TYPE_UNSPECIFIED: MetadataExchangeRequest.AuthType
    AUTO_IAM: MetadataExchangeRequest.AuthType
    DB_NATIVE: MetadataExchangeRequest.AuthType
    OAUTH2_TOKEN_FIELD_NUMBER: _ClassVar[int]
    USER_AGENT_FIELD_NUMBER: _ClassVar[int]
    auth_type: MetadataExchangeRequest.AuthType
    oauth2_token: str
    user_agent: str
    def __init__(
        self,
        user_agent: _Optional[str] = ...,
        auth_type: _Optional[_Union[MetadataExchangeRequest.AuthType, str]] = ...,
        oauth2_token: _Optional[str] = ...,
    ) -> None: ...

class MetadataExchangeResponse(_message.Message):
    __slots__ = ["error", "response_code"]

    class ResponseCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = []
    ERROR: MetadataExchangeResponse.ResponseCode
    ERROR_FIELD_NUMBER: _ClassVar[int]
    OK: MetadataExchangeResponse.ResponseCode
    RESPONSE_CODE_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_CODE_UNSPECIFIED: MetadataExchangeResponse.ResponseCode
    error: str
    response_code: MetadataExchangeResponse.ResponseCode
    def __init__(
        self,
        response_code: _Optional[
            _Union[MetadataExchangeResponse.ResponseCode, str]
        ] = ...,
        error: _Optional[str] = ...,
    ) -> None: ...
