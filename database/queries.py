import pandas as pd


# Explicit projection avoids schema drift and keeps a stable output order for UI/details.
DEMO_AUDIT_COLUMNS = [
    "SEQ_NO",
    "SHIPMENT_ID",
    "PLAN_ID",
    "REQUEST_ID",
    "RULES_FILE",
    "TOTAL_ROUTE_SEGMENTS",
    "STATUS",
    "ERR_MSG",
    "CRTD_DTT",
    "CREATED_BY",
    "UPDT_DTT",
    "UPDATED_BY",
    "SOURCE_FILE_NAME",
    "EMAIL_SENT",
    "ORIGIN",
    "DESTINATION",
    "LOGISTICS_GROUP",
    "DIVISION_CODE",
    "CHARGE_OVERRIDE",
    "EQUIPMENT_TYPE_CODE",
    "RECORD_STATUS",
    "LOAD_ID",
    "LOAD_CREATED",
    "SHIPMENT_CREATED",
    "SUPPORT_GROUP",
]


def run_shipaudit_query(
    conn,
    shipment_id: str = None,
    origin: str = None,
    destination: str = None,
    limit: int = None,
    exclude_success_messages: bool = False,
) -> pd.DataFrame:
    """
    Queries ACME_OMS.DEMO_AUDIT with optional filters.
    At least one filter should be provided — unless `limit` is set, in
    which case the most recent `limit` rows (by CRTD_DTT) are returned
    with no filters, used for the Report tab's "live preview" that shows
    real data as soon as the tab opens.

    `exclude_success_messages` — when True, rows whose ERR_MSG describes
    a SUCCESSFUL outcome (contains "success"/"sucesso", case-insensitive)
    are filtered out AT THE DATABASE LEVEL, not just hidden in the UI
    afterwards. The Troubleshooter/Batch tabs (which feed this data into
    error matching + the local AI's training/gap-detection) should pass
    True here — otherwise every "shipment created successfully" row
    inflates row/error counts, gets matched (uselessly) against the KB,
    and pollutes what the local AI learns as an "error pattern". The
    general Report tab keeps the default (False) since it's meant to
    show the raw data as-is, successes included.
    """
    base_query = f"SELECT {', '.join(DEMO_AUDIT_COLUMNS)} FROM ACME_OMS.DEMO_AUDIT WHERE 1=1"
    params = {}

    if shipment_id and shipment_id.strip():
        # Support comma-separated list of shipment IDs
        ids = [s.strip() for s in shipment_id.split(",") if s.strip()]
        if len(ids) == 1:
            base_query += " AND SHIPMENT_ID = :shipment_id"
            params["shipment_id"] = ids[0]
        else:
            placeholders = ", ".join([f":id_{i}" for i in range(len(ids))])
            base_query += f" AND SHIPMENT_ID IN ({placeholders})"
            for i, sid in enumerate(ids):
                params[f"id_{i}"] = sid

    if origin and origin.strip():
        base_query += " AND UPPER(ORIGIN) LIKE UPPER(:origin)"
        params["origin"] = f"%{origin.strip()}%"

    if destination and destination.strip():
        base_query += " AND UPPER(DESTINATION) LIKE UPPER(:destination)"
        params["destination"] = f"%{destination.strip()}%"

    if exclude_success_messages:
        # NULL-safe: keep rows with no ERR_MSG at all, only drop rows
        # whose ERR_MSG text itself indicates success (mirrors
        # troubleshooter.loader.is_success_message's intent, applied
        # server-side so it never even reaches the DataFrame/local AI).
        base_query += (
            " AND (ERR_MSG IS NULL OR ("
            " UPPER(ERR_MSG) NOT LIKE '%SUCCESS%'"
            " AND UPPER(ERR_MSG) NOT LIKE '%SUCESSO%'"
            "))"
        )

    if limit:
        base_query += " ORDER BY CRTD_DTT DESC FETCH FIRST :row_limit ROWS ONLY"
        params["row_limit"] = int(limit)

    cursor = conn.cursor()
    try:
        cursor.execute(base_query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()

    return pd.DataFrame(rows, columns=columns)


def _split_csv_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [v.strip() for v in str(raw).split(",") if v.strip()]


def _add_in_filter(sql_parts: list[str], params: dict, column_sql: str, bind_prefix: str, values: list[str]) -> None:
    if not values:
        return
    bind_names = []
    for idx, value in enumerate(values):
        bind_name = f"{bind_prefix}_{idx}"
        bind_names.append(f":{bind_name}")
        params[bind_name] = value
    sql_parts.append(f" AND {column_sql} IN ({', '.join(bind_names)})")


def _add_like_filter(sql_parts: list[str], params: dict, column_sql: str, bind_name: str, value: str | None) -> None:
    if value and str(value).strip():
        params[bind_name] = f"%{str(value).strip()}%"
        sql_parts.append(f" AND UPPER({column_sql}) LIKE UPPER(:{bind_name})")


def run_tariff_query(
    conn,
    master_rate_card_ids: str,
    carrier_code: str | None = None,
    origin_zone_code: str | None = None,
    origin_country_code: str | None = None,
    destination_zone_code: str | None = None,
    destination_country_code: str | None = None,
    service_code: str | None = None,
    charge_code: str | None = None,
    equipment_type_code: str | None = None,
    rate_code: str | None = None,
) -> pd.DataFrame:
    """
    Runs the Rate Card Lookup Query with editable filters for non-technical users.

    Args are free-text fields from UI. `master_rate_card_ids` supports comma-separated values.
    """
    ids = _split_csv_values(master_rate_card_ids)
    if not ids:
        return pd.DataFrame()

    params: dict[str, str] = {}
    query_parts = [
        """
        SELECT DISTINCT
            T.RATE_CARD_CODE,
            T.RATE_CARD_ID,
            T.EFFECTIVE_DATE,
            T.EXPIRATION_DATE,
            T.CARRIER_CODE,
            L.ORIGIN_ZONE_CODE,
            L.ORIGIN_COUNTRY_CODE,
            L.DESTINATION_ZONE_CODE,
            L.DESTINATION_COUNTRY_CODE,
            L.SERVICE_CODE,
            R.CHARGE_CODE,
            R.EQUIPMENT_TYPE_CODE,
            R.EQUIPMENT_TYPE_CODE AS EQUIPMENT,
            R.MINIMUM_CHARGE_AMOUNT,
            RT.BREAK_AMOUNT,
            RT.RANGE_CODE,
            C.CURRENCY_CODE,
            R.EFFECTIVE_DATE AS RATE_EFF,
            R.EXPIRATION_DATE AS RATE_EXP,
            RT.RANGE_END,
            L.SERVICE_GRADE,
            L.COMMODITY_CODE,
            L.BASE_DIVISION_CODE,
            R.RATE_CODE
        FROM RTG_APP.DEMO_ROUTE_RATE L
        JOIN RTG_APP.DEMO_RATE_CARD T
            ON T.RATE_CARD_ID = L.RATE_CARD_ID
        JOIN RTG_APP.DEMO_RATE R
            ON R.SERVICE_CODE = L.SERVICE_CODE
            AND R.RATE_CODE = L.RATE_CODE
            AND R.RATE_CARD_ID = L.RATE_CARD_ID
        JOIN RTG_APP.DEMO_RATE_BREAK RT
            ON R.RATE_RECORD_ID = RT.RATE_RECORD_ID
        JOIN RTG_APP.DEMO_CURRENCY C
            ON C.CURRENCY_TYPE = R.CURRENCY_TYPE
        WHERE 1 = 1
        """
    ]

    _add_in_filter(query_parts, params, "T.MASTER_RATE_CARD_ID", "mstr", ids)
    _add_like_filter(query_parts, params, "T.CARRIER_CODE", "carrier_code", carrier_code)
    _add_like_filter(query_parts, params, "L.ORIGIN_ZONE_CODE", "origin_zone_code", origin_zone_code)
    _add_like_filter(query_parts, params, "L.ORIGIN_COUNTRY_CODE", "origin_country_code", origin_country_code)
    _add_like_filter(query_parts, params, "L.DESTINATION_ZONE_CODE", "destination_zone_code", destination_zone_code)
    _add_like_filter(query_parts, params, "L.DESTINATION_COUNTRY_CODE", "destination_country_code", destination_country_code)
    _add_like_filter(query_parts, params, "L.SERVICE_CODE", "service_code", service_code)
    _add_like_filter(query_parts, params, "R.CHARGE_CODE", "charge_code", charge_code)
    _add_like_filter(query_parts, params, "R.EQUIPMENT_TYPE_CODE", "equipment_type_code", equipment_type_code)
    _add_like_filter(query_parts, params, "R.RATE_CODE", "rate_code", rate_code)

    query = "\n".join(query_parts)

    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()

    return pd.DataFrame(rows, columns=columns)

