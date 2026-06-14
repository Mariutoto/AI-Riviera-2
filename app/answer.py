import os

import requests

from app.text_cleaning import fix_mojibake


SYSTEM_PROMPT = """Tu es AI Riviera, un assistant civique.
Réponds uniquement avec les extraits fournis. Si les sources ne permettent pas de répondre, dis-le clairement.
Pour une question générale ou de synthèse, utilise les extraits comme échantillon documentaire: donne une réponse utile, mentionne les grandes catégories observées, et précise les limites au lieu de répondre seulement que c'est impossible.
Réponds dans la langue de la question, de façon concise, et cite les sources pertinentes avec le marqueur "(PDF)" en fin de phrase.
Dans ce contexte communal, "vote", "voté" ou "votation" désignent par défaut les votes/décisions du Conseil communal ou de ses commissions. Ne les interprète comme référendum, scrutin populaire ou vote citoyen que si la question le demande explicitement.
Les sources sont numérotées par document unique: plusieurs passages sous la même source ne sont pas des doublons."""
SYSTEM_PROMPT += """
Quand une source est marquée "source canonique", utilise-la en priorité pour identifier l'objet politique, son statut, ses auteurs et ses dates. Les sources marquées "document lié" servent seulement à compléter avec des détails de rapport, de commission ou de décision."""

QUERY_REWRITE_PROMPT = """Tu aides AI Riviera à préparer une recherche documentaire.
Les règles structurées de l'application n'ont pas su répondre directement.
Reformule la question en une seule requête de recherche autonome, en français.
Garde les noms propres, dates, années, types de documents et mots-clés importants.
Supprime les mots vagues ou conversationnels.
Ne réponds pas à la question.
Retourne seulement la requête reformulée, sans guillemets ni explication."""


def get_secret(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st

        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass

    return default


def document_key(result: dict) -> str:
    metadata = result.get("metadata", {})
    return (
        metadata.get("document_hash")
        or result.get("document_hash")
        or result.get("source_url")
        or metadata.get("source_url")
        or metadata.get("pdf_url")
        or metadata.get("text_path")
        or result.get("relative_text_path")
        or metadata.get("filename")
        or result["id"].split("#", 1)[0]
    )


def group_results_by_document(results: list[dict]) -> list[dict]:
    grouped = {}
    for result in results:
        key = document_key(result)
        metadata = result.get("metadata") or {
            "city": result.get("city", ""),
            "doc_type": result.get("doc_type", ""),
            "title": result.get("title", ""),
            "date": result.get("date", ""),
            "source_url": result.get("source_url", ""),
            "document_hash": result.get("document_hash", ""),
        }
        if key not in grouped:
            grouped[key] = {
                "metadata": metadata,
                "relative_text_path": result.get("relative_text_path", ""),
                "score": result.get("score", 0),
                "passages": [],
            }
        grouped[key]["score"] = max(grouped[key]["score"], result.get("score", 0))
        grouped[key]["passages"].append(result)
    return sorted(grouped.values(), key=lambda item: item["score"], reverse=True)


def build_context(results: list[dict]) -> str:
    blocks = []
    for index, source in enumerate(group_results_by_document(results), start=1):
        metadata = source["metadata"]
        title = metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document")
        year = metadata.get("year") or metadata.get("date", "")
        category = metadata.get("category") or metadata.get("doc_type", "")
        url = metadata.get("source_url") or metadata.get("pdf_url") or metadata.get("url") or ""
        source_kind = ""
        if metadata.get("canonical_object") is True:
            source_kind = " | source canonique"
        elif metadata.get("canonical_object") is False:
            source_kind = " | document lié"
        passages = "\n\n".join(
            f"Passage {passage_index}:\n{passage.get('text') or passage.get('content', '')}"
            for passage_index, passage in enumerate(source["passages"], start=1)
        )
        blocks.append(
            f"[Source {index}] {title} | {year} | {category}{source_kind} | {url}\n"
            f"{passages}"
        )
    return "\n\n---\n\n".join(blocks)


def answer_with_openai(question: str, results: list[dict]) -> str | None:
    api_key = get_secret("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=get_secret("OPENAI_MODEL", "gpt-4.1-mini"),
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Question:\n{question}\n\n"
                        f"Extraits disponibles:\n{build_context(results)}"
                    ),
                },
            ],
        )
        return response.output_text
    except Exception as exc:
        return f"Je n'ai pas pu appeler OpenAI pour générer une synthèse: {exc}"


def answer_with_mistral(question: str, results: list[dict]) -> str | None:
    api_key = get_secret("MISTRAL_API_KEY")
    if not api_key:
        return None

    try:
        model = get_secret("MISTRAL_MODEL", "mistral-small-latest")
        response = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{question}\n\n"
                            f"Extraits disponibles:\n{build_context(results)}"
                        ),
                    },
                ],
                "temperature": 0.2,
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        return f"Je n'ai pas pu appeler Mistral pour générer une synthèse: {exc}"


def rewrite_query_with_openai(question: str) -> str | None:
    api_key = get_secret("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=get_secret("OPENAI_REWRITE_MODEL", get_secret("OPENAI_MODEL", "gpt-4.1-mini")),
            input=[
                {"role": "system", "content": QUERY_REWRITE_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_output_tokens=120,
        )
        rewritten = response.output_text.strip()
        return rewritten or None
    except Exception:
        return None


def rewrite_query_with_mistral(question: str) -> str | None:
    api_key = get_secret("MISTRAL_API_KEY")
    if not api_key:
        return None

    try:
        model = get_secret("MISTRAL_REWRITE_MODEL", get_secret("MISTRAL_MODEL", "mistral-small-latest"))
        response = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": QUERY_REWRITE_PROMPT},
                    {"role": "user", "content": question},
                ],
                "max_tokens": 120,
                "temperature": 0,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        rewritten = data["choices"][0]["message"]["content"].strip()
        return rewritten or None
    except Exception:
        return None


def rewrite_query_with_llm(question: str) -> str | None:
    provider = get_secret("LLM_PROVIDER", "auto").lower().strip()
    if provider in {"none", "off", "extracts"}:
        return None

    if provider == "mistral":
        rewritten = rewrite_query_with_mistral(question)
    elif provider == "openai":
        rewritten = rewrite_query_with_openai(question)
    else:
        rewritten = rewrite_query_with_mistral(question) or rewrite_query_with_openai(question)

    if not rewritten:
        return None
    rewritten = " ".join(rewritten.split())
    if not rewritten or len(rewritten) > 500:
        return None
    return rewritten


def test_mistral_connection() -> tuple[bool, str]:
    api_key = get_secret("MISTRAL_API_KEY")
    if not api_key:
        return False, "MISTRAL_API_KEY n'est pas visible par l'application."

    model = get_secret("MISTRAL_MODEL", "mistral-small-latest")
    try:
        response = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Réponds seulement: OK"}],
                "max_tokens": 8,
                "temperature": 0,
            },
            timeout=30,
        )
        if not response.ok:
            return False, f"Mistral a répondu {response.status_code}: {response.text[:500]}"

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return True, f"Connexion Mistral OK avec `{model}`. Réponse: {content}"
    except Exception as exc:
        return False, f"Erreur pendant le test Mistral: {exc}"


def answer_with_llm(question: str, results: list[dict]) -> str | None:
    provider = get_secret("LLM_PROVIDER", "auto").lower().strip()

    if provider == "mistral":
        return answer_with_mistral(question, results)
    if provider == "openai":
        return answer_with_openai(question, results)
    if provider in {"none", "off", "extracts"}:
        return None

    return answer_with_mistral(question, results) or answer_with_openai(question, results)


def llm_status() -> dict:
    provider = get_secret("LLM_PROVIDER", "auto").lower().strip()
    has_mistral = bool(get_secret("MISTRAL_API_KEY"))
    has_openai = bool(get_secret("OPENAI_API_KEY"))

    if provider == "mistral":
        active = "mistral" if has_mistral else "missing MISTRAL_API_KEY"
    elif provider == "openai":
        active = "openai" if has_openai else "missing OPENAI_API_KEY"
    elif provider in {"none", "off", "extracts"}:
        active = "extracts only"
    elif has_mistral:
        active = "mistral"
    elif has_openai:
        active = "openai"
    else:
        active = "extracts only"

    return {
        "provider": provider,
        "active": active,
        "has_mistral_key": has_mistral,
        "has_openai_key": has_openai,
        "mistral_model": get_secret("MISTRAL_MODEL", "mistral-small-latest"),
        "openai_model": get_secret("OPENAI_MODEL", "gpt-4.1-mini"),
    }


def answer_from_sources(question: str, results: list[dict]) -> str:
    ai_answer = answer_with_llm(question, results)
    if ai_answer:
        return f"{fix_mojibake(ai_answer)}\n\n{_sources_section(results)}" if results else fix_mojibake(ai_answer)

    if not results:
        return "Je ne sais pas: je n'ai pas trouvé de source suffisamment pertinente dans les documents indexés."

    lines = [
        "Je n'ai pas de clé Mistral/OpenAI active, donc j'affiche seulement les meilleurs passages retrouvés. Vérifie les sources avant de considérer la réponse comme correcte.",
        "",
    ]
    for index, result in enumerate(results, start=1):
        metadata = result["metadata"]
        filename = fix_mojibake(metadata.get("filename") or metadata.get("title") or result.get("relative_text_path", "document"))
        year = metadata.get("year") or metadata.get("date", "")
        excerpt = fix_mojibake((result.get("text") or result.get("content", "")).strip().replace("\n", " "))
        lines.append(f"{index}. {filename} ({year})")
        lines.append(f"   {excerpt}")
    lines.append("")
    lines.append(_sources_section(results))
    return "\n".join(lines)


def _sources_section(results: list[dict]) -> str:
    grouped = group_results_by_document(results)
    if not grouped:
        return "Sources utilisées: aucune source disponible."

    lines = ["Sources utilisées:"]
    for index, source in enumerate(grouped, start=1):
        metadata = source["metadata"]
        title = metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document")
        source_url = metadata.get("source_url") or metadata.get("pdf_url") or metadata.get("url") or ""
        label = f"{index}. {title}"
        if source_url:
            label = f"{label} - {source_url} (PDF)"
        lines.append(label)
    return "\n".join(lines)
