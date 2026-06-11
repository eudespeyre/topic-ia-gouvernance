import csv
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = "gpt-4.1"
NB_RUNS = 1

MCP_URL = os.getenv("MCP_URL")
client = OpenAI()

prompt_file = "prompts/system_A.txt"
questions_file = "questions.csv"

with open(prompt_file, "r", encoding="utf-8") as f:
    system_prompt = f.read()

instructions = (
    system_prompt
    + """

Tu disposes d'un serveur MCP data.gouv.fr.

Utilise ce MCP pour travailler exclusivement dans le périmètre documentaire du topic `univers-culture-deps`.

Méthode attendue :

1. identifier les datasets candidats potentiellement pertinents dans le topic ;
2. confronter les datasets candidats au regard :
   - du niveau territorial demandé ;
   - de l’année demandée ;
   - du type d’indicateur recherché ;
   - des ressources et colonnes effectivement disponibles ;
3. vérifier les ressources et colonnes disponibles via le catalogue contextuel et les ressources tabulaires ;
4. sélectionner le dataset et la ressource les plus pertinents au regard du contexte documentaire observé ;
5. interroger ensuite uniquement les ressources tabulaires nécessaires ;
6. répondre uniquement à partir des données effectivement obtenues.
7. En cas d’impossibilité de répondre avec suffisamment de confiance :

- expliquer explicitement pourquoi ;
- citer les datasets et ressources candidats explorés ;
- proposer les datasets ou ressources les plus proches à vérifier manuellement.

Même en cas de refus, conserver une logique d’orientation documentaire et de traçabilité.

Contraintes :

- Ne pas s’arrêter au premier dataset plausible.
- Ne jamais supposer l’existence de colonnes non observées.
- Ne jamais mobiliser de source extérieure au topic.
- Si plusieurs datasets proches existent, évaluer leur pertinence relative avant sélection.
- Si les données disponibles ne permettent pas de répondre, l’indiquer explicitement.
"""
)


def ask_with_native_mcp(question):
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        tools=[
            {
                "type": "mcp",
                "server_label": "datagouv",
                "server_url": MCP_URL,
                "require_approval": "never",
            }
        ],
        input=question,
        max_output_tokens=512,
    )

    usage = response.usage

    return {
        "answer": response.output_text,
        "status": response.status,
        "error": response.error,
        "output": response.output,
        "prompt_tokens": usage.input_tokens if usage else None,
        "response_tokens": usage.output_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
    }


Path("outputs_openai_mcp").mkdir(exist_ok=True)

metadata_csv = "outputs_openai_mcp/metadata.csv"
metadata_exists = Path(metadata_csv).exists()

with open(metadata_csv, "a", newline="", encoding="utf-8") as metadata_file:
    fieldnames = [
        "timestamp",
        "configuration",
        "run",
        "question_id",
        "question",
        "model",
        "status",
        "error",
        "prompt_tokens",
        "response_tokens",
        "total_tokens",
        "markdown_file",
    ]

    writer = csv.DictWriter(metadata_file, fieldnames=fieldnames)

    if not metadata_exists:
        writer.writeheader()

    with open(questions_file, newline="", encoding="utf-8") as questions_csv:
        reader = csv.DictReader(questions_csv)
        rows = list(reader)

        for row in rows:
            question_id = row["id"]
            question = row["question"]

            for run in range(1, NB_RUNS + 1):
                print(f"\n=== D_NATIVE_MCP | {question_id} | run {run:02d} ===")
                print(question)

                result = ask_with_native_mcp(question)

                print("Status:", result["status"])
                print("Error:", result["error"])

                answer = result["answer"]

                print("\nRéponse :")
                print(answer)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                md_filename = f"{timestamp}_D_NATIVE_MCP_{question_id}_run{run:02d}.md"

                with open(
                    f"outputs_openai_mcp/{md_filename}",
                    "w",
                    encoding="utf-8"
                ) as md_file:
                    md_file.write(answer)
                    md_file.write("\n\n---\n\n")
                    md_file.write("## Sortie technique complète\n\n")
                    md_file.write(str(result["output"]))

                writer.writerow(
                    {
                        "timestamp": timestamp,
                        "configuration": "D_NATIVE_MCP",
                        "run": run,
                        "question_id": question_id,
                        "question": question,
                        "model": MODEL,
                        "status": result["status"],
                        "error": result["error"],
                        "prompt_tokens": result["prompt_tokens"],
                        "response_tokens": result["response_tokens"],
                        "total_tokens": result["total_tokens"],
                        "markdown_file": md_filename,
                    }
                )
                time.sleep(8)

print("\nTerminé.")