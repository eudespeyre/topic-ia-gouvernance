import json
import requests
import os

MCP_URL = os.getenv("MCP_URL")

MODEL = "mistral"
TOPIC_ID = "univers-culture-deps"

TOOLS_DESCRIPTION = """
Tu es un agent d'analyse documentaire connecté au MCP data.gouv.fr.

Tu dois répondre uniquement en JSON strictement valide.
Interdiction d'utiliser des commentaires JSON.
Interdiction d'utiliser "..." comme valeur d'argument.
Interdiction d'ajouter du texte hors du JSON.

Topic autorisé :
univers-culture-deps

Outils disponibles :

1. get_topic_catalog
Arguments :
{
  "topic_id": "univers-culture-deps"
}

2. list_topic_elements
Arguments :
{
  "topic_id": "univers-culture-deps",
  "page": 1,
  "page_size": 100,
  "class_": "Dataset"
}

3. query_resource_data
Arguments :
{
  "resource_id": "identifiant exact d'une ressource",
  "page": 1,
  "page_size": 100,
  "sort_column": "nom_colonne_optionnel",
  "sort_direction": "asc ou desc"
}

Règles obligatoires :
- Tu dois utiliser au moins un outil MCP valide avant de répondre.
- Tu ne dois jamais appeler query_resource_data sans resource_id exact.
- Tu ne dois jamais utiliser un topic différent de univers-culture-deps.
- Pour répondre à une question chiffrée, tu dois appeler query_resource_data.
- Si un appel outil échoue, tu dois corriger ton prochain appel.
- Si l'information n'est pas vérifiable après usage du MCP, dis-le explicitement.

Format obligatoire pour appeler un outil :

{
  "action": "tool_call",
  "tool_name": "get_topic_catalog",
  "arguments": {
    "topic_id": "univers-culture-deps"
  }
}

Format obligatoire pour répondre :

{
  "action": "final",
  "answer": "réponse finale courte"
}
"""


def ask_model(messages):
    prompt = "\n\n".join(messages)

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": 256
            }
        },
        timeout=300,
    )

    response.raise_for_status()
    return response.json()["response"].strip()


def call_mcp_tool(tool_name, arguments):
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
    return response.text


def validate_tool_call(tool_name, arguments):
    if tool_name not in {
        "get_topic_catalog",
        "list_topic_elements",
        "query_resource_data",
    }:
        return False, f"Outil non autorisé : {tool_name}"

    if tool_name in {"get_topic_catalog", "list_topic_elements"}:
        if arguments.get("topic_id") != TOPIC_ID:
            return False, (
                f"Topic interdit : {arguments.get('topic_id')}. "
                f"Le seul topic autorisé est {TOPIC_ID}."
            )

    if tool_name == "query_resource_data":
        if not arguments.get("resource_id"):
            return False, (
                "Appel interdit : query_resource_data nécessite un resource_id exact."
            )

        forbidden_keys = {
            "filter_year",
            "region",
            "dataset",
            "topic_id",
        }

        for key in forbidden_keys:
            if key in arguments:
                return False, (
                    f"Argument interdit pour query_resource_data : {key}."
                )

    return True, ""


def run_agent(question):
    messages = [
        TOOLS_DESCRIPTION,
        f"Question utilisateur : {question}",
        """
Commence par appeler un outil MCP valide.
Réponds uniquement en JSON strictement valide.
"""
    ]

    max_steps = 6
    valid_mcp_calls_done = 0

    for step in range(max_steps):
        print(f"\n=== STEP {step + 1} ===")

        model_output = ask_model(messages)

        print("\nRéponse modèle :")
        print(model_output)

        try:
            action = json.loads(model_output)
        except Exception:
            messages.append(
                """
Erreur contrôlée : ta réponse n'est pas un JSON strictement valide.
Tu dois répondre uniquement avec un objet JSON valide, sans commentaire et sans texte autour.
"""
            )
            continue

        if action.get("action") == "final":
            if valid_mcp_calls_done == 0:
                messages.append(
                    """
Erreur contrôlée : tu ne peux pas répondre sans avoir utilisé au moins un outil MCP valide.
Commence par appeler get_topic_catalog ou list_topic_elements sur le topic univers-culture-deps.
Réponds uniquement en JSON valide.
"""
                )
                continue

            return action.get("answer", "")

        if action.get("action") != "tool_call":
            messages.append(
                """
Erreur contrôlée : action inconnue.
Utilise uniquement "tool_call" ou "final".
Réponds uniquement en JSON valide.
"""
            )
            continue

        tool_name = action.get("tool_name")
        arguments = action.get("arguments", {})

        is_valid, error_message = validate_tool_call(tool_name, arguments)

        if not is_valid:
            print("\nErreur contrôlée :")
            print(error_message)

            messages.append(
                f"""
Erreur contrôlée : {error_message}

Corrige ton prochain appel.
Tu dois utiliser uniquement le topic {TOPIC_ID}.
Tu dois répondre uniquement en JSON valide.
"""
            )
            continue

        print(f"\nAppel outil : {tool_name}")
        print(arguments)

        tool_result = call_mcp_tool(tool_name, arguments)
        valid_mcp_calls_done += 1

        print("\nRésultat outil :")
        print(tool_result[:2000])

        messages.append(
            f"""
Résultat outil {tool_name} :

{tool_result}

À partir de ce résultat, décide si tu dois appeler un autre outil ou répondre en final.
Réponds uniquement en JSON valide.
"""
        )

    return "Nombre maximal d'étapes atteint sans réponse finale valide."


question = "Quelle région présente la dépense culturelle totale la plus élevée en 2023 ?"

answer = run_agent(question)

print("\n=== RÉPONSE FINALE ===")
print(answer)