import csv
import json
import os
from datetime import datetime
from pathlib import Path

import requests

CONFIGURATIONS = ["A", "D"]
MODEL = "mistral"
NB_RUNS = 2

TOPIC_ID = "univers-culture-deps"
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8007/mcp")

prompt_file = "prompts/system_A.txt"

with open(prompt_file, "r", encoding="utf-8") as f:
    system_prompt = f.read()


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


def get_context_for_D():
    topic_elements = call_mcp_tool(
        "list_topic_elements",
        {
            "topic_id": TOPIC_ID,
            "page": 1,
            "page_size": 100,
            "class_": "Dataset",
        },
    )

    topic_catalog = call_mcp_tool(
        "get_topic_catalog",
        {
            "topic_id": TOPIC_ID,
        },
    )

    context = f"""
Périmètre documentaire : topic data.gouv.fr `{TOPIC_ID}`.

Éléments du topic :
{topic_elements}

Catalogue de contextualisation :
{topic_catalog}
"""

    return context


def build_prompt(configuration, question):
    if configuration == "A":
        return system_prompt + "\n\nQuestion : " + question

    if configuration == "D":
        context = get_context_for_D()
        return (
            system_prompt
            + "\n\nContexte documentaire :\n"
            + context
            + "\n\nQuestion : "
            + question
        )

    raise ValueError(f"Configuration inconnue : {configuration}")


Path("outputs").mkdir(exist_ok=True)

questions_file = "questions.csv"
metadata_csv = "outputs/metadata.csv"
metadata_exists = Path(metadata_csv).exists()

with open(metadata_csv, "a", newline="", encoding="utf-8") as metadata_file:
    fieldnames = [
        "timestamp",
        "configuration",
        "run",
        "question_id",
        "question",
        "model",
        "prompt_tokens",
        "response_tokens",
        "total_duration",
        "markdown_file",
    ]

    writer = csv.DictWriter(metadata_file, fieldnames=fieldnames)

    if not metadata_exists:
        writer.writeheader()

    with open(questions_file, newline="", encoding="utf-8") as questions_csv:
        reader = csv.DictReader(questions_csv)
        rows = list(reader)

        for configuration in CONFIGURATIONS:
            for row in rows:
                question_id = row["id"]
                question = row["question"]

                for run in range(1, NB_RUNS + 1):
                    print(f"\n=== {configuration} | {question_id} | run {run:02d} ===")
                    print(question)

                    final_prompt = build_prompt(configuration, question)

                    response = requests.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": MODEL,
                            "prompt": final_prompt,
                            "stream": False,
                            "options": {
                                "num_predict": 512
                            },
                        },
                        timeout=300,
                    )

                    data = response.json()
                    answer = data["response"]

                    print("\nRéponse :")
                    print(answer)

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    md_filename = (
                        f"{timestamp}_{configuration}_{question_id}_run{run:02d}.md"
                    )

                    with open(f"outputs/{md_filename}", "w", encoding="utf-8") as md_file:
                        md_file.write(answer)

                    writer.writerow(
                        {
                            "timestamp": timestamp,
                            "configuration": configuration,
                            "run": run,
                            "question_id": question_id,
                            "question": question,
                            "model": MODEL,
                            "prompt_tokens": data.get("prompt_eval_count"),
                            "response_tokens": data.get("eval_count"),
                            "total_duration": data.get("total_duration"),
                            "markdown_file": md_filename,
                        }
                    )

print("\nTerminé.")
