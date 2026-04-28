import asyncio

from flexus_client_kit import ckit_client
from flexus_client_kit import ckit_bot_install
from flexus_client_kit import ckit_cloudtool

from flexus_my_bots.hubseg import hubseg_bot
from flexus_my_bots.hubseg import hubseg_prompts


TOOL_NAMESET = {t.name for t in hubseg_bot.TOOLS}

EXPERTS = [
    (
        "default",
        ckit_bot_install.FMarketplaceExpertInput(
            fexp_system_prompt=hubseg_prompts.HUBSEG_PROMPT,
            fexp_python_kernel="",
            fexp_allow_tools=",".join(TOOL_NAMESET | ckit_cloudtool.KANBAN_SAFE),
            fexp_nature="NATURE_INTERACTIVE",
            fexp_inactivity_timeout=3600,
            fexp_description=(
                "Expert for creating and managing HubSpot audience segments "
                "from natural language requests."
            ),
        ),
    ),
]


async def install(client: ckit_client.FlexusClient):
    r = await ckit_bot_install.marketplace_upsert_dev_bot(
        client,
        ws_id=client.ws_id,
        bot_dir=hubseg_bot.HUBSEG_ROOTDIR,
        marketable_accent_color="#FF7A59",
        marketable_title1="HubSeg",
        marketable_title2="Audience Segmentation for HubSpot",
        marketable_author="Development",
        marketable_occupation="CRM Segmentation Specialist",
        marketable_description=(hubseg_bot.HUBSEG_ROOTDIR / "README.md").read_text(),
        marketable_typical_group="CRM / Marketing",
        marketable_setup_default=hubseg_bot.HUBSEG_SETUP_SCHEMA,
        marketable_featured_actions=[
            {"feat_question": "Show all my HubSpot segments"},
            {"feat_question": "Create a segment of all CMOs from Germany"},
            {"feat_question": "Create a segment of all new leads this month"},
        ],
        marketable_intro_message=(
            "Hi! I\u2019m HubSeg \u2014 I create and manage audience segments in HubSpot. "
            "Describe who you want to target in plain text and I\u2019ll build the dynamic list for you. "
            "Example: \u2018Create a segment of all CMOs from Germany\u2019 or \u2018Show me all current segments\u2019."
        ),
        marketable_preferred_model_expensive="gpt-5.4",
        marketable_preferred_model_cheap="gpt-5.4-nano",
        marketable_experts=[
            (name, exp.filter_tools(hubseg_bot.TOOLS)) for name, exp in EXPERTS
        ],
        marketable_schedule=[],
        marketable_tags=["HubSpot", "CRM", "Marketing", "Segmentation"],
        marketable_auth_supported=["hubspot"],
    )
    return r.marketable_version


if __name__ == "__main__":
    client = ckit_client.FlexusClient("hubseg_install")
    asyncio.run(install(client))
