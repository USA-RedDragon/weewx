#
#    Copyright (c) 2009-2024 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""weedb driver for the PostgreSQL database"""

import psycopg2
from psycopg2 import errorcodes

from weeutil.weeutil import to_bool
import weedb

exception_map = {
    errorcodes.CONNECTION_EXCEPTION: weedb.CannotConnectError,
    errorcodes.SQLCLIENT_UNABLE_TO_ESTABLISH_SQLCONNECTION: weedb.CannotConnectError,
    errorcodes.CONNECTION_DOES_NOT_EXIST: weedb.CannotConnectError,
    errorcodes.CANNOT_CONNECT_NOW: weedb.CannotConnectError,
    errorcodes.SQLSERVER_REJECTED_ESTABLISHMENT_OF_SQLCONNECTION: weedb.DisconnectError,
    errorcodes.CONNECTION_FAILURE: weedb.DisconnectError,
    errorcodes.PROTOCOL_VIOLATION: weedb.DisconnectError,
    errorcodes.IDLE_SESSION_TIMEOUT: weedb.DisconnectError,
    errorcodes.ADMIN_SHUTDOWN: weedb.DisconnectError,
    errorcodes.CRASH_SHUTDOWN: weedb.DisconnectError,
    errorcodes.DATABASE_DROPPED: weedb.DisconnectError,
    errorcodes.INVALID_PASSWORD: weedb.BadPasswordError,
    errorcodes.MODIFYING_SQL_DATA_NOT_PERMITTED: weedb.PermissionError,
    errorcodes.PROHIBITED_SQL_STATEMENT_ATTEMPTED: weedb.PermissionError,
    errorcodes.READING_SQL_DATA_NOT_PERMITTED: weedb.PermissionError,
    errorcodes.CONTAINING_SQL_NOT_PERMITTED: weedb.PermissionError,
    errorcodes.INSUFFICIENT_PRIVILEGE: weedb.PermissionError,
    None: weedb.DatabaseError
}


def guard(fn):
    """Decorator function that converts PostgreSQL exceptions into weedb exceptions."""

    def guarded_fn(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except psycopg2.Error as e:
            # Get the PostgreSQL exception number out of e:
            try:
                errno = e.pgcode
            except (AttributeError):
                errno = None
            # Default exception is weedb.DatabaseError
            klass = exception_map.get(errno, weedb.DatabaseError)
            raise klass(e)

    return guarded_fn


def connect(host='localhost', user='', password='', database_name='',
            port=5432, driver='', **kwargs):
    """Connect to the specified database"""
    return Connection(host=host, port=int(port), user=user, password=password,
                      database_name=database_name, **kwargs)


def create(host='localhost', user='', password='', database_name='',
           port=5432, driver='', **kwargs):
    """Create the specified database. If it already exists,
    an exception of type weedb.DatabaseExistsError will be raised."""

    # Open up a connection w/o specifying the database.
    with Connection(host=host,
                    port=int(port),
                    user=user,
                    password=password,
                    **kwargs) as connect:
        with connect.cursor() as cursor:
            # Now create the database.
            cursor.execute("CREATE DATABASE %s" % (database_name,))


def drop(host='localhost', user='', password='', database_name='',
         port=5432, driver='', **kwargs):
    """Drop (delete) the specified database."""

    with Connection(host=host,
                    port=int(port),
                    user=user,
                    password=password,
                    **kwargs) as connect:
        with connect.cursor() as cursor:
            cursor.execute("DROP DATABASE %s" % database_name)


class Connection(weedb.Connection):
    """A wrapper around a PostgreSQL connection object."""

    @guard
    def __init__(self, host='localhost', user='', password='', database_name='',
                 port=5432, **kwargs):
        """Initialize an instance of Connection.

        Args:
            host (str): IP or hostname hosting the PostgreSQL database.
                Alternatively, the path to the socket mount. (required)
            user (str): The username (required)
            password (str): The password for the username (required)
            database_name (str): The database to be used. (required)
            port (int): Its port number (optional; default is 5432)
            kwargs (dict):   Any extra arguments you may wish to pass on to psycopg2's
              connect statement (optional).
        """
        connection = psycopg2.connect(host=host, port=int(port), user=user, password=password,
                                     dbname=database_name, **kwargs)

        weedb.Connection.__init__(self, connection, database_name, 'postgresql')

    def cursor(self):
        """Return a cursor object."""
        return Cursor(self)

    @guard
    def tables(self):
        """Returns a list of tables in the database."""

        table_list = list()
        # Get a cursor directly from PostgreSQL
        with self.connection.cursor() as cursor:
            cursor.execute("select relname from pg_class where relkind='r' and relname !~ '^(pg_|sql_)';")
            while True:
                row = cursor.fetchone()
                if row is None: break
                # Extract the table name. In case it's in unicode, convert to a regular string.
                table_list.append(str(row[0]))
        return table_list

    @guard
    def genSchemaOf(self, table):
        """Return a summary of the schema of the specified table.
        
        If the table does not exist, an exception of type weedb.OperationalError is raised."""

        with self.connection.cursor() as cursor:
            cursor.execute("""
                SELECT t.column_name, t.data_type, t.is_nullable, p.constraint_name = '%s_pkey', t.column_default
                FROM information_schema.columns AS t
                LEFT JOIN information_schema.key_column_usage AS p
                ON p.column_name = t.column_name
                WHERE t.table_name = '%s' AND (p.constraint_name = '%s_pkey' OR p.constraint_name IS NULL);
            """ % (table, table, table))
            if cursor.rowcount == 0:
                raise weedb.ProgrammingError("Table %s does not exist" % table)
            irow = 0
            while True:
                row = cursor.fetchone()
                if row is None:
                    break
                print(row)
                # Append this column to the list of columns.
                colname = str(row[0])
                coltype = str(row[1]).upper()
                is_primary = False if row[3] == '' else to_bool(row[3])
                can_be_null = False if row[2] == '' else to_bool(row[2])
                yield (irow, colname, coltype, can_be_null, row[4], is_primary)
                irow += 1

    @guard
    def columnsOf(self, table):
        """Return a list of columns in the specified table. 
        
        If the table does not exist, an exception of type weedb.OperationalError is raised."""
        column_list = [row[1] for row in self.genSchemaOf(table)]
        return column_list

    @guard
    def get_variable(self, var_name):
        with self.connection.cursor() as cursor:
            cursor.execute("SHOW %s;" % var_name)
            row = cursor.fetchone()
            # This is actually a 2-way tuple (variable-name, variable-value),
            # or None, if the variable does not exist.
            return row

    @guard
    def begin(self):
        """Begin a transaction."""
        self.connection.cursor().execute("START TRANSACTION")

    @guard
    def commit(self):
        self.connection.commit()

    @guard
    def rollback(self):
        self.connection.rollback()

class Cursor(weedb.Cursor):
    """A wrapper around the MySQLdb cursor object"""

    @guard
    def __init__(self, connection):
        """Initialize a Cursor from a connection.
        
        connection: An instance of db.mysql.Connection"""

        self.cursor = connection.connection.cursor()

    @guard
    def execute(self, sql_string, sql_tuple=()):
        """Execute a SQL statement on the MySQL server.
        
        sql_string: A SQL statement to be executed. It should use ? as
        a placeholder.
        
        sql_tuple: A tuple with the values to be used in the placeholders."""

        # Weewx uses backticks for identifiers, but Postgres does not, so replace the `'s with "
        postgres_string = sql_string.replace('`', '"')

        self.cursor.execute(postgres_string, sql_tuple)

        return self

    def fetchone(self):
        # Get a result from the MySQL cursor, then run it through the _massage
        # filter below
        return self.cursor.fetchone()

    def close(self):
        try:
            self.cursor.close()
            del self.cursor
        except AttributeError:
            pass

    #
    # Supplying functions __iter__ and next allows the cursor to be used as an iterator.
    #
    def __iter__(self):
        return self

    def __next__(self):
        result = self.fetchone()
        if result is None:
            raise StopIteration
        return result

    def __enter__(self):
        return self

    def __exit__(self, etyp, einst, etb):
        self.close()
