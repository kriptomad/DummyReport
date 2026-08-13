"""
Support queries derived from SQL transport files.
Each function receives a connection + user-friendly parameters and returns pd.DataFrame.
"""
import pandas as pd


# ─────────────────────────────────────────────────────────────
#  QUERY 1 — Shipment Full Details
#  Source: Shipment search ADM2.sql
#  Use: Always run when analysing a SHIPMENT_ID
# ─────────────────────────────────────────────────────────────
SHIPMENT_DETAILS_SQL = """
SELECT DISTINCT
    shipment.shpm_num,
    shipment.cust_cd,
    shipment.status,
    shipment.eq_typ_cd,
    shipment.frm_shpg_loc_cd,
    shipment.frm_name,
    shipment.frm_ctry_cd,
    shipment.frm_sta_cd,
    shipment.frm_cty_name,
    shipment.to_shpg_loc_cd,
    shipment.to_name,
    shipment.to_ctry_cd,
    shipment.to_sta_cd,
    shipment.to_cty_name,
    shipment.pref_ap_carr_cd,
    shipment.pref_ap_srv_cd,
    shipment.chg_ovr_chg_cd,
    shipment.frm_pkup_dtt,
    shipment.to_pkup_dtt,
    shipment.frm_dlvy_dtt,
    shipment.to_dlvy_dtt,
    shipment.crtd_dtt,
    shipment.crtd_usr_cd,
    shipment.updt_dtt,
    shipment.updt_usr_cd,
    shipment.scld_wgt,
    shipment.vol,
    shipment.urgt_yn,
    shipment.frht_trms_enu,
    shipment.shpm_desc,
    shipment.csld_cls
FROM
    tms_oms.shipment shipment
WHERE
    shipment.shpm_num = :shpm_num
"""


def run_shipment_details(conn, shpm_num: str) -> pd.DataFrame:
    """Full shipment record from TMS_OMS.SHIPMENT by SHPM_NUM."""
    cursor = conn.cursor()
    try:
        cursor.execute(SHIPMENT_DETAILS_SQL, {"shpm_num": shpm_num.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 2 — Shipment History
#  Source: ticket SCTASK2570565.sql (SHIPMENT_HISTORY table)
#  Use: Always run — shows status transitions and when error occurred
# ─────────────────────────────────────────────────────────────
SHIPMENT_HISTORY_SQL = """
SELECT *
FROM TMS_OMS.SHIPMENT_HISTORY
WHERE shpm_num = :shpm_num
ORDER BY shpm_num, trans_id
"""


def run_shipment_history(conn, shpm_num: str) -> pd.DataFrame:
    """Status transition history from TMS_OMS.SHIPMENT_HISTORY."""
    cursor = conn.cursor()
    try:
        cursor.execute(SHIPMENT_HISTORY_SQL, {"shpm_num": shpm_num.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 3 — Load Info (Load Leg)
#  Source: Getting Loads to Tmops Bol Batch.sql
#  Use: Un-routable errors — confirms if load was generated
# ─────────────────────────────────────────────────────────────
LOAD_INFO_SQL = """
SELECT DISTINCT
    llt.ld_leg_id,
    lldt.cust_cd,
    lldt.frm_shpg_loc_cd,
    lldt.to_shpg_loc_cd,
    lldt.srvc_cd,
    llt.carr_cd,
    llt.cur_optlstat_id,
    llt.crtd_dtt,
    llt.updt_dtt,
    llt.tot_scld_wgt
FROM
    i2tm_app.ld_leg_t llt
    JOIN i2tm_app.ld_leg_detl_t lldt ON llt.ld_leg_id = lldt.ld_leg_id
    JOIN i2tm_app.shpm_t st ON lldt.shpm_id = st.shpm_id
WHERE
    st.shpm_num = :shpm_num
ORDER BY
    llt.crtd_dtt DESC
"""


def run_load_info(conn, shpm_num: str) -> pd.DataFrame:
    """Load leg details — checks if a load was created for the shipment."""
    cursor = conn.cursor()
    try:
        cursor.execute(LOAD_INFO_SQL, {"shpm_num": shpm_num.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
 #  QUERY 4 — Location Status (manual — single location code)
#  Source: Sprint 16 queries.sql (shpg_loc_t + addr_t)
#  Use: Manual lookup of any location code
# ─────────────────────────────────────────────────────────────
LOCATION_STATUS_SQL = """
SELECT
    sl.shpg_loc_cd,
    sl.shpg_loc_typ_enu,
    sl.actv_enu,
    sl.cust_cd,
    sl.crtd_dtt,
    sl.updt_dtt,
    a.loc_name,
    a.st_name,
    a.cty_name,
    a.sta_cd,
    a.ctry_cd,
    a.pstl_cd,
    a.loc
FROM
    i2tm_app.shpg_loc_t sl
    INNER JOIN i2tm_app.addr_t a ON sl.addr_id = a.addr_id
WHERE
    sl.shpg_loc_cd = :shpg_loc_cd
"""


def run_location_status(conn, shpg_loc_cd: str) -> pd.DataFrame:
    """Location details — checks if a location code exists and is active."""
    cursor = conn.cursor()
    try:
        cursor.execute(LOCATION_STATUS_SQL, {"shpg_loc_cd": shpg_loc_cd.strip().upper()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 4B — Origin & Destination Validation (automatic)
#  Source: Sprint 16 queries.sql + INTL_DUMMY_DEMO_AUDIT
#  Use: Validates ORIGIN and DESTINATION from the audit record
#       directly — no manual input needed, runs from SHIPMENT_ID
# ─────────────────────────────────────────────────────────────
ORIGIN_DEST_VALIDATION_SQL = """
SELECT
    audit.SHIPMENT_ID,
    audit.ORIGIN                        AS audit_origin,
    audit.DESTINATION                   AS audit_destination,
    audit.ERR_MSG,
    -- ORIGIN validation
    orig_loc.shpg_loc_cd                AS origin_loc_cd,
    orig_loc.actv_enu                   AS origin_actv_enu,
    orig_loc.shpg_loc_typ_enu           AS origin_type,
    orig_addr.loc_name                  AS origin_loc_name,
    orig_addr.ctry_cd                   AS origin_ctry_cd,
    orig_addr.sta_cd                    AS origin_sta_cd,
    orig_addr.cty_name                  AS origin_city,
    -- DESTINATION validation
    dest_loc.shpg_loc_cd                AS dest_loc_cd,
    dest_loc.actv_enu                   AS dest_actv_enu,
    dest_loc.shpg_loc_typ_enu           AS dest_type,
    dest_addr.loc_name                  AS dest_loc_name,
    dest_addr.ctry_cd                   AS dest_ctry_cd,
    dest_addr.sta_cd                    AS dest_sta_cd,
    dest_addr.cty_name                  AS dest_city,
    -- Existence flags
    CASE WHEN orig_loc.shpg_loc_cd IS NULL THEN 'NOT FOUND'
         WHEN UPPER(orig_loc.actv_enu) IN ('0','N','NO','INACTIVE') THEN 'INACTIVE'
         ELSE 'ACTIVE' END              AS origin_status,
    CASE WHEN dest_loc.shpg_loc_cd IS NULL THEN 'NOT FOUND'
         WHEN UPPER(dest_loc.actv_enu) IN ('0','N','NO','INACTIVE') THEN 'INACTIVE'
         ELSE 'ACTIVE' END              AS destination_status
FROM
    ACME_OMS.DEMO_AUDIT audit
    -- ORIGIN join
    LEFT JOIN i2tm_app.shpg_loc_t orig_loc
        ON UPPER(orig_loc.shpg_loc_cd) = UPPER(audit.ORIGIN)
    LEFT JOIN i2tm_app.addr_t orig_addr
        ON orig_loc.addr_id = orig_addr.addr_id
    -- DESTINATION join
    LEFT JOIN i2tm_app.shpg_loc_t dest_loc
        ON UPPER(dest_loc.shpg_loc_cd) = UPPER(audit.DESTINATION)
    LEFT JOIN i2tm_app.addr_t dest_addr
        ON dest_loc.addr_id = dest_addr.addr_id
WHERE
    audit.SHIPMENT_ID = :shipment_id
"""


def run_origin_dest_validation(conn, shipment_id: str) -> pd.DataFrame:
    """
    Validates ORIGIN and DESTINATION from INTL_DUMMY_DEMO_AUDIT against
    i2tm_app.shpg_loc_t. Returns existence + active status for both.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(ORIGIN_DEST_VALIDATION_SQL, {"shipment_id": shipment_id.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 5 — Reference Numbers (SRVC_CD, Equipment, etc.)
#  Source: Preload Services List.sql + Select getRefDataForStops.sql
#  Use: Un-routable errors — cross-reference with Tariff Pool SRVC_CD
# ─────────────────────────────────────────────────────────────
REFERENCE_NUMBERS_SQL = """
SELECT DISTINCT
    st.shpm_num,
    st.shpm_id,
    st.eq_typ_cd,
    st.frm_shpg_loc_cd,
    st.to_shpg_loc_cd,
    rn.rfrc_num_typ,
    rn.rfrc_num
FROM
    i2tm_app.shpm_t st
    LEFT JOIN i2tm_app.rfrc_num_t rn ON st.shpm_id = rn.shpm_id
WHERE
    st.shpm_num = :shpm_num
    AND rn.rfrc_num_typ IN (
        'Container', 'Ocean BOL', 'Vessel', 'LDTP', 'UL',
        'Transshipment Vessel', 'HAZ', 'OCN_CONT_NUM'
    )
ORDER BY
    rn.rfrc_num_typ
"""


def run_reference_numbers(conn, shpm_num: str) -> pd.DataFrame:
    """Reference numbers for a shipment — SRVC_CD, container, ocean BOL, etc."""
    cursor = conn.cursor()
    try:
        cursor.execute(REFERENCE_NUMBERS_SQL, {"shpm_num": shpm_num.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 6 — Load Events / TM Logs
#  Source: Getting Loads to Tmops Bol Batch.sql (i2_evnt_que_t)
#  Use: "Rate exists – TM investigation required" — capture TM logs
# ─────────────────────────────────────────────────────────────
LOAD_EVENTS_SQL = """
SELECT
    ev.root_obj_id,
    ev.evnt_notf_id,
    ev.cpld_dtt,
    ev.evnt_id,
    ev.evnt_typ_cd,
    ev.status,
    ev.msg_txt
FROM
    tms_oms.i2_evnt_que_t ev
    JOIN i2tm_app.ld_leg_detl_t lldt ON ev.root_obj_id = lldt.ld_leg_id
    JOIN i2tm_app.shpm_t st ON lldt.shpm_id = st.shpm_id
WHERE
    st.shpm_num = :shpm_num
ORDER BY
    ev.cpld_dtt DESC
"""


def run_load_events(conn, shpm_num: str) -> pd.DataFrame:
    """TM event queue for a shipment — useful when rate exists but routing failed."""
    cursor = conn.cursor()
    try:
        cursor.execute(LOAD_EVENTS_SQL, {"shpm_num": shpm_num.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  CATALOGUE — maps query key → metadata for UI
# ─────────────────────────────────────────────────────────────
QUERY_CATALOGUE = {
    "shipment_details": {
        "label":       "📦 Shipment Full Details",
        "description": "Full shipment record from TMS_OMS.SHIPMENT — origin, destination, carrier, equipment, status.",
        "param_label": "SHPM_NUM (Shipment Number)",
        "param_key":   "shpm_num",
        "fn":          run_shipment_details,
        "auto_on":     ["all"],
    },
    "shipment_history": {
        "label":       "🕓 Shipment History",
        "description": "Status transition log — shows when the error occurred and all state changes.",
        "param_label": "SHPM_NUM (Shipment Number)",
        "param_key":   "shpm_num",
        "fn":          run_shipment_history,
        "auto_on":     ["all"],
    },
    "origin_dest_validation": {
        "label":       "📍 Origin & Destination Validation",
        "description": (
            "Validates ORIGIN and DESTINATION from the audit record against shpg_loc_t. "
            "Shows if each location EXISTS and is ACTIVE — runs automatically from SHIPMENT_ID."
        ),
        "param_label": "SHIPMENT_ID",
        "param_key":   "shipment_id",
        "fn":          run_origin_dest_validation,
        "auto_on":     ["all"],   # always useful — highlights inactive/missing locations
    },
    "load_info": {
        "label":       "🚛 Load Info (Load Leg)",
        "description": "Load leg details — confirms if a load was generated from the shipment.",
        "param_label": "SHPM_NUM (Shipment Number)",
        "param_key":   "shpm_num",
        "fn":          run_load_info,
        "auto_on":     ["missing rate", "itinerary", "schedule"],
    },
    "location_status": {
        "label":       "📍 Location Status (manual)",
        "description": "Manual lookup — checks if a specific location code exists and is active.",
        "param_label": "SHPG_LOC_CD (Location Code)",
        "param_key":   "shpg_loc_cd",
        "fn":          run_location_status,
        "auto_on":     ["inactive", "dc not found", "master data", "logistics", "division", "equipment"],
    },
    "reference_numbers": {
        "label":       "🔢 Reference Numbers",
        "description": "Reference numbers for the shipment — container, ocean BOL, LDTP, vessel.",
        "param_label": "SHPM_NUM (Shipment Number)",
        "param_key":   "shpm_num",
        "fn":          run_reference_numbers,
        "auto_on":     ["missing rate", "itinerary", "schedule"],
    },
    "load_events": {
        "label":       "📋 Load Events / TM Logs",
        "description": "TM event queue — use when rate exists but routing still failed.",
        "param_label": "SHPM_NUM (Shipment Number)",
        "param_key":   "shpm_num",
        "fn":          run_load_events,
        "auto_on":     ["missing rate", "itinerary", "schedule"],
    },
}


def get_auto_queries_for_category(category: str) -> list[str]:
    """Return list of query keys that should auto-run for this error category."""
    cat_lower = (category or "").lower()
    keys = []
    for key, meta in QUERY_CATALOGUE.items():
        triggers = meta.get("auto_on", [])
        if "all" in triggers:
            keys.append(key)
            continue
        for trigger in triggers:
            if trigger in cat_lower:
                keys.append(key)
                break
    return keys

