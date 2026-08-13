import oracledb


def get_connection(
    host: str,
    port: str,
    service: str,
    user: str,
    password: str,
    timeout: int = 15,
    call_timeout_seconds: int = 30,
    app_cws: str | None = None,
):
    """
    Creates and returns an Oracle DB connection.
    Raises an exception if the connection fails.

    A tcp_connect_timeout is set so the app doesn't hang indefinitely if the
    host/port is unreachable (e.g. VPN not connected, wrong port).

    `app_cws`: the CWS of the person actually operating the app (their own
    login identity inside ILT Troubleshooter/PSLD - Parts/Portal), NOT
    necessarily the Windows account under which this Python process
    happens to be running. python-oracledb lets us tell the DB server
    exactly what to record as OSUSER/MACHINE/PROGRAM for this session
    (sent in the TNS CONNECT_DATA) — so v$session shows the true acting
    person regardless of whether the app is being run from a shared
    support workstation, a central server, or someone's own PC. This is
    what actually prevents Oracle "shared login" compliance alerts from
    misattributing a session to whoever's Windows login the process
    happens to be running under (the real cause of the incident that
    prompted this — see Documentation/APPLICATION_ACCOUNT.md). If
    `app_cws` isn't provided, python-oracledb falls back to its own
    OS-level defaults as before.
    """
    dsn = f"{host}:{port}/{service}"
    connect_kwargs = dict(
        user=user,
        password=password,
        dsn=dsn,
        tcp_connect_timeout=timeout,
    )
    if app_cws:
        connect_kwargs.update(
            osuser=app_cws,
            machine=f"ILT-App:{app_cws}",
            program="ILT-Troubleshooter",
        )
    connection = oracledb.connect(**connect_kwargs)
    connection.call_timeout = max(0, int(call_timeout_seconds)) * 1000
    return connection


def test_connection(
    host: str,
    port: str,
    service: str,
    user: str,
    password: str,
    keep_open: bool = False,
    app_cws: str | None = None,
) -> tuple[bool, str] | tuple[bool, str, oracledb.Connection | None]:
    """
    Tests the Oracle connection.
    Returns (True, "Connected") or (False, error_message).

    See get_connection() for what `app_cws` does (OSUSER/MACHINE/PROGRAM
    identity override sent to the DB server).
    """
    try:
        conn = get_connection(host, port, service, user, password, app_cws=app_cws)
        if keep_open:
            return True, "Connection successful!", conn
        conn.close()
        return True, "Connection successful!"
    except Exception as e:
        if keep_open:
            return False, str(e), None
        return False, str(e)

