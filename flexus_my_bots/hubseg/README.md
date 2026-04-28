# HubSeg — HubSpot Audience Segmentation Bot

HubSeg creates and manages dynamic contact segments in HubSpot based on simple natural language requests. Describe your audience in plain text — HubSeg translates it into precise HubSpot filters and builds the list automatically.

## What it does

- **Create dynamic segments** — describe who you want to target, HubSeg builds the list
- **Filter by demographics** — country, city, company, job title
- **Filter by CRM data** — lead status, lifecycle stage, owner, lead source
- **Manage segments** — list, update, and delete existing lists
- **Get insights** — see contact counts and sample contacts for each segment

## Setup

1. In HubSpot go to **Settings → Integrations → Private Apps**
2. Create a new Private App and enable these scopes:
   - `crm.lists.read` — read lists
   - `crm.lists.write` — create / update / delete lists
   - `crm.objects.contacts.read` — read contacts
3. Copy the generated token
4. Paste it in bot settings under **HubSpot API Key**

## Example requests

- "Create a segment of all CMOs from Germany"
- "Show all my current segments"
- "Create a list of leads assigned to John"
- "Make a segment where lifecycle stage is Customer"
- "Update the Q1 segment — add filter: source is Organic Search"
- "Delete the old test segment"
