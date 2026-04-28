import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from flexus_client_kit import ckit_client
from flexus_client_kit import ckit_cloudtool
from flexus_client_kit import ckit_bot_exec
from flexus_client_kit import ckit_shutdown
from flexus_client_kit import ckit_bot_version

logger = logging.getLogger("bot_hubseg")

BOT_NAME = ckit_bot_version.bot_name_from_file(__file__)
HUBSEG_ROOTDIR = Path(__file__).parent
HUBSEG_SETUP_SCHEMA = json.loads((HUBSEG_ROOTDIR / "setup_schema.json").read_text())

HUBSPOT_BASE = "https://api.hubapi.com"

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

CREATE_SEGMENT_TOOL = ckit_cloudtool.CloudTool(
    strict=True,
    name="hubspot_create_segment",
    description=(
        "Create a new dynamic (auto-updating) contact list/segment in HubSpot. "
        "Filters are ANDed together. Common properties: country, city, company, "
        "jobtitle, lifecyclestage, hs_lead_status, hubspot_owner_id, hs_analytics_source."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name for the new segment/list"},
            "filters": {
                "type": "array",
                "description": "Filter conditions (all are ANDed). Each item specifies a HubSpot contact property condition.",
                "items": {
                    "type": "object",
                    "properties": {
                        "property": {
                            "type": "string",
                            "description": "HubSpot contact property API name, e.g. 'country', 'jobtitle', 'company', 'lifecyclestage'",
                        },
                        "operator": {
                            "type": "string",
                            "enum": [
                                "IS_EQUAL_TO", "IS_NOT_EQUAL_TO",
                                "CONTAINS", "NOT_CONTAIN",
                                "STARTS_WITH", "ENDS_WITH",
                                "IS_ANY_OF", "IS_NONE_OF",
                                "IS_KNOWN", "IS_UNKNOWN",
                            ],
                        },
                        "value": {
                            "type": ["string", "null"],
                            "description": "Filter value. null for IS_KNOWN/IS_UNKNOWN. Comma-separated for IS_ANY_OF/IS_NONE_OF.",
                        },
                    },
                    "required": ["property", "operator", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["name", "filters"],
        "additionalProperties": False,
    },
)

LIST_SEGMENTS_TOOL = ckit_cloudtool.CloudTool(
    strict=True,
    name="hubspot_list_segments",
    description="List all contact lists/segments in HubSpot with name, ID, contact count, and type.",
    parameters={
        "type": "object",
        "properties": {
            "search_query": {
                "type": ["string", "null"],
                "description": "Optional search string to filter lists by name. Pass null to get all lists.",
            },
        },
        "required": ["search_query"],
        "additionalProperties": False,
    },
)

GET_SEGMENT_TOOL = ckit_cloudtool.CloudTool(
    strict=True,
    name="hubspot_get_segment",
    description="Get details of a specific segment/list: its filter criteria, contact count, and sample contacts.",
    parameters={
        "type": "object",
        "properties": {
            "list_id": {"type": "string", "description": "HubSpot list ID"},
            "contacts_preview": {
                "type": "integer",
                "description": "Number of sample contacts to fetch (0-10, default 5)",
            },
        },
        "required": ["list_id", "contacts_preview"],
        "additionalProperties": False,
    },
)

UPDATE_SEGMENT_TOOL = ckit_cloudtool.CloudTool(
    strict=True,
    name="hubspot_update_segment",
    description="Update an existing dynamic segment in HubSpot — rename it or replace its filter criteria.",
    parameters={
        "type": "object",
        "properties": {
            "list_id": {"type": "string", "description": "HubSpot list ID to update"},
            "new_name": {
                "type": ["string", "null"],
                "description": "New name for the list. null = keep existing name.",
            },
            "filters": {
                "type": ["array", "null"],
                "description": "Replacement filter conditions (all ANDed). null = keep existing filters.",
                "items": {
                    "type": "object",
                    "properties": {
                        "property": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": [
                                "IS_EQUAL_TO", "IS_NOT_EQUAL_TO",
                                "CONTAINS", "NOT_CONTAIN",
                                "STARTS_WITH", "ENDS_WITH",
                                "IS_ANY_OF", "IS_NONE_OF",
                                "IS_KNOWN", "IS_UNKNOWN",
                            ],
                        },
                        "value": {"type": ["string", "null"]},
                    },
                    "required": ["property", "operator", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["list_id", "new_name", "filters"],
        "additionalProperties": False,
    },
)

DELETE_SEGMENT_TOOL = ckit_cloudtool.CloudTool(
    strict=True,
    name="hubspot_delete_segment",
    description="Delete a contact list/segment in HubSpot. Requires user confirmation. This cannot be undone.",
    parameters={
        "type": "object",
        "properties": {
            "list_id": {"type": "string", "description": "HubSpot list ID to delete"},
            "list_name": {"type": "string", "description": "List name (shown in confirmation prompt)"},
        },
        "required": ["list_id", "list_name"],
        "additionalProperties": False,
    },
)

TOOLS = [
    CREATE_SEGMENT_TOOL,
    LIST_SEGMENTS_TOOL,
    GET_SEGMENT_TOOL,
    UPDATE_SEGMENT_TOOL,
    DELETE_SEGMENT_TOOL,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_filter_branch(filters: List[Dict]) -> Dict:
    """Convert simplified filter list to HubSpot Lists API v3 filterBranch format."""
    enum_props = {"lifecyclestage", "hs_lead_status", "hs_analytics_source", "hs_buying_role"}
    hs_filters = []
    for f in filters:
        prop = f["property"]
        op = f["operator"]
        val = f.get("value")
        is_enum = prop in enum_props

        if op in ("IS_KNOWN", "IS_UNKNOWN"):
            operation: Dict[str, Any] = {
                "operator": op,
                "operationType": "ALL_PROPERTY",
                "includeObjectsWithNoValueSet": op == "IS_UNKNOWN",
            }
        elif op in ("IS_ANY_OF", "IS_NONE_OF"):
            values = [v.strip() for v in (val or "").split(",") if v.strip()]
            operation = {
                "operator": op,
                "operationType": "ENUMERATION" if is_enum else "MULTI_STRING",
                "values": values,
                "includeObjectsWithNoValueSet": False,
            }
        else:
            operation = {
                "operator": op,
                "operationType": "ENUMERATION" if is_enum else "STRING",
                "value": val or "",
                "includeObjectsWithNoValueSet": False,
            }

        hs_filters.append({
            "filterType": "PROPERTY",
            "property": prop,
            "operation": operation,
        })

    return {
        "filterBranchType": "AND",
        "filterBranches": [],
        "filters": hs_filters,
    }


async def _hs_request(
    token: str,
    method: str,
    path: str,
    body: Optional[Dict] = None,
    params: Optional[Dict] = None,
) -> Dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = HUBSPOT_BASE + path
    async with httpx.AsyncClient(timeout=30.0) as cli:
        resp = await cli.request(method, url, headers=headers, json=body, params=params)
    try:
        data = resp.json()
    except Exception:
        data = {"text": resp.text}
    return {"status": resp.status_code, "data": data}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def hubseg_main_loop(
    fclient: ckit_client.FlexusClient,
    rcx: ckit_bot_exec.RobotContext,
) -> None:
    setup = ckit_bot_exec.official_setup_mixing_procedure(
        HUBSEG_SETUP_SCHEMA, rcx.persona.persona_setup
    )

    def _token() -> str:
        return str(setup.get("hubspot_api_key", "")).strip()

    def _auth_error() -> Optional[str]:
        if not _token():
            return json.dumps({
                "ok": False,
                "error": "HubSpot API key not configured. "
                          "Please add your Private App Token in bot settings (hubspot_api_key).",
            })
        return None

    @rcx.on_tool_call(CREATE_SEGMENT_TOOL.name)
    async def toolcall_create_segment(
        toolcall: ckit_cloudtool.FCloudtoolCall,
        args: Dict[str, Any],
    ) -> str:
        if rcx.running_test_scenario:
            return json.dumps({
                "ok": True,
                "list_id": "12345",
                "name": args["name"],
                "processingType": "DYNAMIC",
                "memberCount": 47,
            })

        err = _auth_error()
        if err:
            return err

        payload = {
            "objectTypeId": "0-1",  # contacts
            "processingType": "DYNAMIC",
            "name": args["name"],
            "filterBranch": _build_filter_branch(args.get("filters", [])),
        }
        r = await _hs_request(_token(), "POST", "/crm/v3/lists", body=payload)
        if r["status"] >= 400:
            return json.dumps({"ok": False, "status": r["status"], "error": r["data"]})
        d = r["data"]
        list_id = str(d.get("listId") or d.get("id", ""))
        return json.dumps({
            "ok": True,
            "list_id": list_id,
            "name": args["name"],
            "processingType": "DYNAMIC",
            "memberCount": d.get("memberCount", "calculating..."),
        })

    @rcx.on_tool_call(LIST_SEGMENTS_TOOL.name)
    async def toolcall_list_segments(
        toolcall: ckit_cloudtool.FCloudtoolCall,
        args: Dict[str, Any],
    ) -> str:
        if rcx.running_test_scenario:
            return json.dumps({
                "ok": True,
                "total": 3,
                "lists": [
                    {"listId": "101", "name": "CMOs in Germany", "processingType": "DYNAMIC", "memberCount": 47},
                    {"listId": "102", "name": "US Leads", "processingType": "DYNAMIC", "memberCount": 234},
                    {"listId": "103", "name": "Marketing Qualified", "processingType": "DYNAMIC", "memberCount": 1205},
                ],
            })

        err = _auth_error()
        if err:
            return err

        params: Dict[str, Any] = {"limit": 200}
        q = args.get("search_query")
        if q:
            params["searchQuery"] = q
        r = await _hs_request(_token(), "GET", "/crm/v3/lists", params=params)
        if r["status"] >= 400:
            return json.dumps({"ok": False, "status": r["status"], "error": r["data"]})
        lists = r["data"].get("lists", [])
        simplified = [
            {
                "listId": l.get("listId"),
                "name": l.get("name"),
                "processingType": l.get("processingType"),
                "memberCount": l.get("memberCount"),
                "createdAt": (l.get("createdAt", "") or "")[:10],
            }
            for l in lists
        ]
        return json.dumps({"ok": True, "total": len(simplified), "lists": simplified})

    @rcx.on_tool_call(GET_SEGMENT_TOOL.name)
    async def toolcall_get_segment(
        toolcall: ckit_cloudtool.FCloudtoolCall,
        args: Dict[str, Any],
    ) -> str:
        if rcx.running_test_scenario:
            return json.dumps({
                "ok": True,
                "listId": args["list_id"],
                "name": "CMOs in Germany",
                "processingType": "DYNAMIC",
                "memberCount": 47,
                "sample_contacts": [
                    {"firstname": "Anna", "lastname": "Schmidt", "email": "a.schmidt@beispiel.de",
                     "jobtitle": "CMO", "company": "Beispiel GmbH", "country": "Germany"},
                    {"firstname": "Karl", "lastname": "M\u00fcller", "email": "k.mueller@firma.de",
                     "jobtitle": "Chief Marketing Officer", "company": "Firma AG", "country": "Germany"},
                ],
            })

        err = _auth_error()
        if err:
            return err

        tok = _token()
        list_id = args["list_id"]
        r = await _hs_request(tok, "GET", f"/crm/v3/lists/{list_id}")
        if r["status"] >= 400:
            return json.dumps({"ok": False, "status": r["status"], "error": r["data"]})
        d = r["data"]

        preview_n = min(max(int(args.get("contacts_preview") or 5), 0), 10)
        contacts = []
        if preview_n > 0:
            mr = await _hs_request(tok, "GET", f"/crm/v3/lists/{list_id}/memberships",
                                   params={"limit": preview_n})
            if mr["status"] < 400:
                for m in mr["data"].get("results", [])[:preview_n]:
                    mid = str(m.get("recordId") or m.get("id", ""))
                    if not mid:
                        continue
                    cr = await _hs_request(
                        tok, "GET", f"/crm/v3/objects/contacts/{mid}",
                        params={"properties": "firstname,lastname,email,jobtitle,company,country"},
                    )
                    if cr["status"] < 400:
                        p = cr["data"].get("properties", {})
                        contacts.append({
                            "firstname": p.get("firstname", ""),
                            "lastname": p.get("lastname", ""),
                            "email": p.get("email", ""),
                            "jobtitle": p.get("jobtitle", ""),
                            "company": p.get("company", ""),
                            "country": p.get("country", ""),
                        })

        return json.dumps({
            "ok": True,
            "listId": d.get("listId"),
            "name": d.get("name"),
            "processingType": d.get("processingType"),
            "memberCount": d.get("memberCount"),
            "filterBranch": d.get("filterBranch", {}),
            "sample_contacts": contacts,
        })

    @rcx.on_tool_call(UPDATE_SEGMENT_TOOL.name)
    async def toolcall_update_segment(
        toolcall: ckit_cloudtool.FCloudtoolCall,
        args: Dict[str, Any],
    ) -> str:
        if rcx.running_test_scenario:
            return json.dumps({"ok": True, "list_id": args["list_id"], "message": "Segment updated successfully."})

        err = _auth_error()
        if err:
            return err

        tok = _token()
        list_id = args["list_id"]
        ops = []

        if args.get("new_name"):
            r = await _hs_request(
                tok, "PUT", f"/crm/v3/lists/{list_id}/update-list-name",
                body={"name": args["new_name"]},
            )
            ops.append({"action": "rename", "ok": r["status"] < 400, "status": r["status"]})

        if args.get("filters") is not None:
            r = await _hs_request(
                tok, "PUT", f"/crm/v3/lists/{list_id}/update-list-filters",
                body={"filterBranch": _build_filter_branch(args["filters"])},
            )
            ops.append({
                "action": "update_filters",
                "ok": r["status"] < 400,
                "status": r["status"],
                "error": r["data"] if r["status"] >= 400 else None,
            })

        if not ops:
            return json.dumps({"ok": False, "error": "Nothing to update: provide new_name or filters."})

        all_ok = all(o["ok"] for o in ops)
        return json.dumps({"ok": all_ok, "list_id": list_id, "operations": ops})

    @rcx.on_tool_call(DELETE_SEGMENT_TOOL.name)
    async def toolcall_delete_segment(
        toolcall: ckit_cloudtool.FCloudtoolCall,
        args: Dict[str, Any],
    ) -> str:
        if not toolcall.confirmed_by_human:
            raise ckit_cloudtool.NeedsConfirmation(
                confirm_setup_key="",
                confirm_command=f"delete_segment:{args['list_id']}",
                confirm_explanation=(
                    f"Delete HubSpot segment \u00ab{args['list_name']}\u00bb (ID: {args['list_id']})? "
                    "This cannot be undone."
                ),
            )

        if rcx.running_test_scenario:
            return json.dumps({"ok": True, "message": f"Segment '{args['list_name']}' deleted."})

        err = _auth_error()
        if err:
            return err

        r = await _hs_request(_token(), "DELETE", f"/crm/v3/lists/{args['list_id']}")
        ok = r["status"] < 400
        return json.dumps({
            "ok": ok,
            "status": r["status"],
            "message": f"Segment '{args['list_name']}' deleted successfully." if ok else "Delete failed.",
            "error": r["data"] if not ok else None,
        })

    try:
        while not ckit_shutdown.shutdown_event.is_set():
            await rcx.unpark_collected_events(sleep_if_no_work=10.0)
    finally:
        logger.info("%s exit", rcx.persona.persona_id)


def main():
    from flexus_my_bots.hubseg import hubseg_install

    scenario_fn = ckit_bot_exec.parse_bot_args()
    bot_version = ckit_bot_version.read_version_file(__file__)
    fclient = ckit_client.FlexusClient(
        ckit_client.bot_service_name(BOT_NAME, bot_version),
        endpoint="/v1/jailed-bot",
    )
    asyncio.run(
        ckit_bot_exec.run_bots_in_this_group(
            fclient,
            bot_main_loop=hubseg_main_loop,
            inprocess_tools=TOOLS,
            scenario_fn=scenario_fn,
            install_func=hubseg_install.install,
        )
    )


if __name__ == "__main__":
    main()
