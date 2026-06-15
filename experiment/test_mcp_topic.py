"""
Test direct du serveur MCP topic-aware.

Ce script ne constitue pas une évaluation d'un modèle de langage.
Il vise uniquement à vérifier que le serveur MCP custom, lancé localement,
expose correctement les outils liés aux topics data.gouv.fr.

Objectif méthodologique :
- valider l'accès au périmètre documentaire via list_topic_elements ;
- valider l'accès au catalogue de contextualisation via get_topic_catalog ;
- séparer la validation de l'infrastructure MCP de l'évaluation agentique
  réalisée ensuite avec un modèle de langage.

Ce test correspond donc à une étape de contrôle technique préalable :
il vérifie que les outils sont disponibles et retournent les artefacts attendus,
avant d'observer comment un agent ou un LLM les mobilise dans une trajectoire
de réponse.
"""

import json
import os

import requests

MCP_BASE_URL = os.getenv("MCP_BASE_URL", "http://127.0.0.1:8007")
MCP_URL = os.getenv("MCP_URL", f"{MCP_BASE_URL}/mcp")
TOPIC_ID = os.getenv("TOPIC_ID", "univers-culture-deps")


def call_mcp_tool(tool_name, arguments):
    """Appelle directement un outil MCP, sans modèle de langage."""
    response = requests.post(
        MCP_URL,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        },
        timeout=60,
    )
    response.raise_for_status()

    for line in response.text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            content_text = payload["result"]["content"][0]["text"]
            return json.loads(content_text)

    raise RuntimeError(f"No data line found in MCP response: {response.text[:500]}")


def main():
    print("=== 1. Healthcheck du serveur MCP ===")
    health = requests.get(f"{MCP_BASE_URL}/health", timeout=10)
    health.raise_for_status()
    print(json.dumps(health.json(), indent=2, ensure_ascii=False))

    print("\n=== 2. Test du périmètre documentaire : list_topic_elements ===")
    elements = call_mcp_tool(
        "list_topic_elements",
        {
            "topic_id": TOPIC_ID,
            "page": 1,
            "page_size": 10,
            "class_": "Dataset",
        },
    )

    print(f"topic_id: {TOPIC_ID}")
    print(f"page: {elements.get('page')}")
    print(f"page_size: {elements.get('page_size')}")
    print(f"total datasets: {elements.get('total')}")
    print("first dataset ids:")
    for item in elements.get("data", [])[:5]:
        element = item.get("element", {})
        print(f"- {element.get('id')}")

    print("\n=== 3. Test du catalogue de contextualisation : get_topic_catalog ===")
    catalog = call_mcp_tool(
        "get_topic_catalog",
        {
            "topic_id": TOPIC_ID,
        },
    )

    print(f"status: {catalog.get('status')}")
    print(f"topic_id: {catalog.get('topic_id')}")
    print(f"catalog_dataset_id: {catalog.get('catalog_dataset_id')}")
    print(f"catalog_version: {catalog.get('catalog_version')}")
    print("resources:")
    for resource in catalog.get("resources", []):
        print(
            f"- {resource.get('title')} | "
            f"{resource.get('format')} | "
            f"{resource.get('url')}"
        )

    print("\n=== Résultat méthodologique ===")
    print(
        "Le test confirme uniquement la disponibilité des outils MCP "
        "et des artefacts documentaires. Il ne mesure pas encore la qualité "
        "d'une réponse produite par un modèle."
    )


if __name__ == "__main__":
    main()
