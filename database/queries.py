import pandas as pd


# Explicit projection avoids schema drift and keeps a stable output order for UI/details.
DEMO_AUDIT_COLUMNS = [
    "SEQ_NO",
    "SHIPMENT_ID",
    "PLAN_ID",
    "OPT_REQ_ID",
    "CONSTRAINTS_FILE",
    "TOTAL_NO_OF_SHPM_LEGS",
    "STATUS",
    "ERR_MSG",
    "CRTD_DTT",
    "CRTD_BY",
    "UPDT_DTT",
    "UPDT_BY",
    "EXCEL_FILE_NAME",
    "EMAIL_TRIGGERED",
    "ORIGIN",
    "DESTINATION",
    "LOGISTICS_GROUP",
    "DIV_CD",
    "CHRG_OVRD",
    "EQUIP_TYP_CD",
    "RECORD_STATUS",
    "LOAD_ID",
    "LOAD_CRTD",
    "SHIP_CRTD",
    "RESP_GRP",
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
    mstr_tff_ids: str,
    carr_cd: str | None = None,
    orig_zn_cd: str | None = None,
    orig_ctry_cd: str | None = None,
    dest_zn_cd: str | None = None,
    dest_ctry_cd: str | None = None,
    srvc_cd: str | None = None,
    chrg_cd: str | None = None,
    eqmt_typ_cd: str | None = None,
    rate_cd: str | None = None,
) -> pd.DataFrame:
    """
    Runs the Tariff Pool Query with editable filters for non-technical users.

    Args are free-text fields from UI. `mstr_tff_ids` supports comma-separated values.
    """
    ids = _split_csv_values(mstr_tff_ids)
    if not ids:
        return pd.DataFrame()

    params: dict[str, str] = {}
    query_parts = [
        """
        SELECT DISTINCT
            T.TFF_CD,
            T.TFF_ID,
            T.EFCT_DT,
            T.EXPD_DT,
            T.CARR_CD,
            L.ORIG_ZN_CD,
            L.ORIG_CTRY_CD,
            L.DEST_ZN_CD,
            L.DEST_CTRY_CD,
            L.SRVC_CD,
            R.CHRG_CD,
            R.EQMT_TYP_CD,
            R.EQMT_TYP_CD AS EQUIPMENT,
            R.MIN_CHRG_DLR,
            RT.BRK_AMT_DLR,
            RT.RNG_CD,
            C.CNCY_CD,
            R.EFCT_DT AS RATE_EFF,
            R.EXPD_DT AS RATE_EXP,
            RT.RNG_TO,
            L.SRVC_GRD_TYP,
            L.CDTY_CD,
            L.BASE_DIV_CD,
            R.RATE_CD
        FROM I2TM_APP.LANE_ASSC_T L
        JOIN I2TM_APP.TFF_T T
            ON T.TFF_ID = L.TFF_ID
        JOIN I2TM_APP.RATE_T R
            ON R.SRVC_CD = L.SRVC_CD
            AND R.RATE_CD = L.RATE_CD
            AND R.TFF_ID = L.TFF_ID
        JOIN I2TM_APP.RNG_RATE_T RT
            ON R.RATE_ID = RT.RATE_ID
        JOIN I2TM_APP.CNCY_T C
            ON C.CNCY_TYP = R.CNCY_TYP
        WHERE 1 = 1
        """
    ]

    _add_in_filter(query_parts, params, "T.MSTR_TFF_ID", "mstr", ids)
    _add_like_filter(query_parts, params, "T.CARR_CD", "carr_cd", carr_cd)
    _add_like_filter(query_parts, params, "L.ORIG_ZN_CD", "orig_zn_cd", orig_zn_cd)
    _add_like_filter(query_parts, params, "L.ORIG_CTRY_CD", "orig_ctry_cd", orig_ctry_cd)
    _add_like_filter(query_parts, params, "L.DEST_ZN_CD", "dest_zn_cd", dest_zn_cd)
    _add_like_filter(query_parts, params, "L.DEST_CTRY_CD", "dest_ctry_cd", dest_ctry_cd)
    _add_like_filter(query_parts, params, "L.SRVC_CD", "srvc_cd", srvc_cd)
    _add_like_filter(query_parts, params, "R.CHRG_CD", "chrg_cd", chrg_cd)
    _add_like_filter(query_parts, params, "R.EQMT_TYP_CD", "eqmt_typ_cd", eqmt_typ_cd)
    _add_like_filter(query_parts, params, "R.RATE_CD", "rate_cd", rate_cd)

    query = "\n".join(query_parts)

    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()

    return pd.DataFrame(rows, columns=columns)

