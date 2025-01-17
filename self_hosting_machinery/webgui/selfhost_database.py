import os
import asyncio
import uuid
import logging

from typing import List, Dict, Any, Optional
from datetime import datetime

from cassandra.cluster import Cluster, Session
from cassandra.cluster import NoHostAvailable
from cassandra.cluster import DCAwareRoundRobinPolicy
from cassandra.auth import PlainTextAuthProvider

from cassandra.cqlengine import columns, connection
from cassandra.cqlengine.management import sync_table
from cassandra.cqlengine.models import Model


os.environ['CQLENG_ALLOW_SCHEMA_MANAGEMENT'] = '1'


def init_model(
        model_cls,
        keyspace: str,
        connection: str,  # noqa;
):
    model_cls.__keyspace__ = keyspace
    model_cls.__connection__ = connection
    sync_table(model_cls, keyspaces=[keyspace], connections=[connection])
    return model_cls


class UsersAccessControl(Model):
    account = columns.Text(primary_key=True)
    team = columns.Text()
    api_key = columns.Text()


class TelemetryNetwork(Model):
    id = columns.Text(primary_key=True)
    tenant_name = columns.Text()
    team = columns.Text(default="")
    ts_reported = columns.DateTime()
    ip = columns.Text()
    enduser_client_version = columns.Text()

    counter = columns.Integer()
    error_message = columns.Text()
    scope = columns.Text()
    success = columns.Boolean()
    url = columns.Text()

    teletype = columns.Text()
    ts_start = columns.Integer()
    ts_end = columns.Integer()


class TelemetrySnippets(Model):
    id = columns.Text(primary_key=True)
    tenant_name = columns.Text()
    team = columns.Text(default="")
    ts_reported = columns.DateTime()
    ip = columns.Text()
    enduser_client_version = columns.Text()

    model = columns.Text()
    corrected_by_user = columns.Text()
    remaining_percentage = columns.Float()
    created_ts = columns.Integer()
    accepted_ts = columns.Integer()
    finished_ts = columns.Integer()
    grey_text = columns.Text()
    cursor_character = columns.Integer()
    cursor_file = columns.Text()
    cursor_line = columns.Integer()
    multiline = columns.Boolean()
    sources = columns.Text()

    teletype = columns.Text()


class TelemetryRobotHuman(Model):
    id = columns.Text(primary_key=True)
    tenant_name = columns.Text()
    team = columns.Text(default="")
    ts_reported = columns.DateTime()
    ip = columns.Text()
    enduser_client_version = columns.Text()

    completions_cnt = columns.Integer()
    file_extension = columns.Text()
    human_characters = columns.Integer()
    model = columns.Text()
    robot_characters = columns.Integer()

    teletype = columns.Text()
    ts_start = columns.Integer()
    ts_end = columns.Integer()


class TelemetryCompCounters(Model):
    id = columns.Text(primary_key=True)
    tenant_name = columns.Text()
    team = columns.Text(default="")
    ts_reported = columns.DateTime()
    ip = columns.Text()
    enduser_client_version = columns.Text()

    counters_json_text = columns.Text()
    file_extension = columns.Text()
    model = columns.Text()
    multiline = columns.Boolean()

    teletype = columns.Text()
    ts_end = columns.Integer()
    ts_start = columns.Integer()


class DisableLogger:

    def __enter__(self):
        logging.disable(logging.CRITICAL)

    def __exit__(self, exit_type, exit_value, exit_traceback):
        logging.disable(logging.NOTSET)


class RefactDatabase:
    KEYSPACE = os.environ.get("REFACT_KEYSPACE", "smc")
    CONN_NAME = "refactdb_connection"

    def __init__(self):
        # NOTE: this is a hack to wait for a db to be ready
        self._session = None
        self._cluster = None
        self._conn_registered = False

    async def connect(self):
        while True:
            try:
                auth_provider = PlainTextAuthProvider(
                    username="cassandra", password="cassandra")
                self._cluster = Cluster(
                    contact_points=[self._database_host],
                    port=self._database_port, auth_provider=auth_provider, protocol_version=4,
                    load_balancing_policy=DCAwareRoundRobinPolicy(local_dc='datacenter1'))
                with DisableLogger():
                    self._session = self._cluster.connect()
                connection.register_connection(self.CONN_NAME, session=self._session)
                self._conn_registered = True
                break
            except NoHostAvailable:
                logging.warning(f"No database available on {self._database_host}:{self._database_port}, "
                                f"sleep for 10 seconds...")
                await asyncio.sleep(10)

        self._create_and_set_keyspace()

    def __del__(self):
        if self._session:
            self._session.shutdown()
        if self._cluster:
            self._cluster.shutdown()
        if self._conn_registered:
            connection.unregister_connection(self.CONN_NAME)

    @property
    def _database_host(self) -> str:
        return os.environ.get("REFACT_DATABASE_HOST", "127.0.0.1")

    @property
    def _database_port(self) -> int:
        return int(os.environ.get("REFACT_DATABASE_PORT", 9042))

    def _create_and_set_keyspace(self):
        self._session.execute(f"""
            CREATE KEYSPACE IF NOT EXISTS {self.KEYSPACE}
            WITH replication = {{ 'class': 'SimpleStrategy', 'replication_factor': '2' }}
        """)
        self._session.set_keyspace(self.KEYSPACE)

    @property
    def session(self) -> Session:
        return self._session


class StatisticsService:

    def __init__(self, database: RefactDatabase):
        self._database: RefactDatabase = database
        self._net: Optional[Model] = None
        self._snip: Optional[Model] = None
        self._rh: Optional[Model] = None
        self._comp: Optional[Model] = None

    def init_models(self):
        assert self._database.session is not None
        self._net = init_model(TelemetryNetwork, self._database.KEYSPACE, self._database.CONN_NAME)
        self._snip = init_model(TelemetrySnippets, self._database.KEYSPACE, self._database.CONN_NAME)
        self._rh = init_model(TelemetryRobotHuman, self._database.KEYSPACE, self._database.CONN_NAME)
        self._comp = init_model(TelemetryCompCounters, self._database.KEYSPACE, self._database.CONN_NAME)

    @property
    def is_ready(self) -> bool:
        return self._comp is not None

    def network_insert(self, telemetry_network: TelemetryNetwork):
        self._net.create(**{
            **telemetry_network._as_dict(),
            "id": str(uuid.uuid1()),
            "ts_reported": datetime.now(),
        })

    def snippets_insert(self, telemetry_snippets: TelemetrySnippets):
        self._snip.create(**{
            **telemetry_snippets._as_dict(),
            "id": str(uuid.uuid1()),
            "ts_reported": datetime.now(),
        })

    def robot_human_insert(self, telemetry_robot_human: TelemetryRobotHuman):
        self._rh.create(**{
            **telemetry_robot_human._as_dict(),
            "id": str(uuid.uuid1()),
            "ts_reported": datetime.now(),
        })

    def comp_counters_insert(self, telemetry_comp_counters: TelemetryCompCounters):
        self._comp.create(**{
            **telemetry_comp_counters._as_dict(),
            "id": str(uuid.uuid1()),
            "ts_reported": datetime.now(),
        })

    @property
    def session(self) -> Session:
        return self._database.session
