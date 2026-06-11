import csv
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CONFIGURATIONS = ["D"]
MODEL = "gpt-4.1"
NB_RUNS = 1

client = OpenAI()

prompt_file = "prompts/system_A.txt"
context_file = "contexte.md"

with open(prompt_file, "r", encoding="utf-8") as f:
    system_prompt = f.read()

with open(context_file, "r", encoding="utf-8") as f:
    contexte_documentaire = f.read()

contexte_documentaire = contexte_documentaire.replace(
    "${MCP_URL}",
    os.getenv("MCP_URL", "")
)


def build_prompt(configuration, question):
    if configuration == "A":
        return system_prompt + "\n\nQuestion : " + question

    if configuration == "D":
        return (
            system_prompt
            + "\n\nContexte documentaire disponible :\n"
            + contexte_documentaire
            + "\n\nQuestion : "
            + question
        )

    raise ValueError(f"Configuration inconnue : {configuration}")


def generate_with_openai(prompt):
    response = client.responses.create(
        model=MODEL,
        input=prompt,
        max_output_tokens=512,
    )

    print("Status:", response.status)
    print("Error:", response.error)

    usage = response.usage

    return {
        "answer": response.output_text,
        "prompt_tokens": usage.input_tokens if usage else None,
        "response_tokens": usage.output_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
    }


Path("outputs_gpt55").mkdir(exist_ok=True)

questions_file = "questions.csv"
metadata_csv = "outputs_gpt55/metadata.csv"
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
        "total_tokens",
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

                    result = generate_with_openai(final_prompt)

                    answer = result["answer"]

                    print("\nRéponse :")
                    print(answer)

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                    md_filename = (
                        f"{timestamp}_{configuration}_{question_id}_run{run:02d}.md"
                    )

                    with open(
                        f"outputs_gpt55/{md_filename}",
                        "w",
                        encoding="utf-8"
                    ) as md_file:
                        md_file.write(answer)

                    writer.writerow(
                        {
                            "timestamp": timestamp,
                            "configuration": configuration,
                            "run": run,
                            "question_id": question_id,
                            "question": question,
                            "model": MODEL,
                            "prompt_tokens": result["prompt_tokens"],
                            "response_tokens": result["response_tokens"],
                            "total_tokens": result["total_tokens"],
                            "markdown_file": md_filename,
                        }
                    )

print("\nTerminé.")