import json
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
SYSTEM_PROMPT += """
N'invente jamais de noms de personnes, co-auteurs, dates, chiffres ou faits absents des extraits fournis. Si un auteur est identifié dans les extraits seulement comme un groupe ou un parti (sans nom propre), ne lui attribue pas de nom propre inventé."""
SYSTEM_PROMPT += """
Reste bref dans le corps de la réponse: une synthèse de quelques phrases suffit. Chaque source est déjà listée séparément avec son titre, son auteur, sa date et une courte description — ne recopie pas ces détails ni le contenu complet de chaque extrait dans le texte de la réponse."""

QUERY_REWRITE_PROMPT = """Tu aides AI Riviera à préparer une recherche documentaire.
Reformule la question en une seule requête de recherche autonome, en français.
Garde uniquement les noms propres, dates, années, types de documents et mots-clés déjà présents dans la question.
N'invente et n'ajoute aucune date, année, lieu, pays ou fait qui n'est pas explicitement mentionné dans la question.
Supprime les mots vagues ou conversationnels.
Ne réponds pas à la question.
Retourne seulement la requête reformulée, sans guillemets ni explication."""

LLM_RERANK_PROMPT = """Tu rerankes des extraits documentaires pour AI Riviera.
Choisis les extraits les plus utiles pour répondre précisément à la question.
Privilégie:
- le document exact cité dans la question;
- les sources canoniques et les documents au titre directement lié;
- les extraits qui contiennent des faits vérifiables;
- la diversité de documents quand la question demande comparaison ou synthèse.
Écarte les documents seulement vaguement liés.
Base ton choix uniquement sur les extraits fournis; n'invente et ne suppose aucun fait, date, lieu ou identifiant absent des candidats.

Retourne uniquement un tableau JSON d'identifiants, par ordre de pertinence, par exemple: ["1", "4", "2"].
Ne retourne aucun texte hors JSON."""

CLASSIFY_PROMPT = """Tu classes une question pour AI Riviera avant de lancer la recherche documentaire.
Détermine si la question porte sur un seul sujet, ou si elle demande explicitement de comparer ou de croiser deux éléments distincts (par exemple deux auteurs, deux types de documents, deux objets nommés, "à la fois X et Y").
Choisis "complex" seulement quand la question demande vraiment ce croisement. Dans le doute, choisis "simple": une fausse "complex" fragmente une question simple pour rien, alors qu'une fausse "simple" se contente de lancer la recherche normale.
Si "complex", propose exactement deux sous-requêtes de recherche autonomes en français, une par facette, sans guillemets ni explication dans chaque requête.

Retourne uniquement un objet JSON, par exemple:
{"complexity": "simple", "mode": "single"}
ou
{"complexity": "complex", "mode": "multi", "subqueries": [{"label": "...", "query": "..."}, {"label": "...", "query": "..."}]}
Ne retourne aucun texte hors JSON."""

VERIFICATION_PROMPT = """Tu vérifies une réponse d'AI Riviera avant qu'elle soit affichée à l'utilisateur.
On te donne la question, la réponse rédigée, et les extraits sources sur lesquels elle devait se baser.
Liste chaque affirmation de la réponse portant sur un nom de personne, un parti, une date, un chiffre ou un type de document qui n'est PAS directement confirmé par les extraits fournis.
N'invente rien toi-même: si tu n'es pas certain qu'une affirmation soit non supportée, ne la liste pas.

Retourne uniquement un objet JSON, par exemple:
{"unsupported_claims": []}
ou
{"unsupported_claims": ["le co-auteur \\"Gabriel Chervet\\" n'apparaît dans aucun extrait"]}
Ne retourne aucun texte hors JSON."""

BROADEN_QUERY_PROMPT = """Tu aides AI Riviera à relancer une recherche documentaire qui a donné trop peu de résultats.
Reformule la question en une requête de recherche plus large: garde les mots-clés essentiels mais enlève les contraintes les plus spécifiques (année précise, auteur précis, titre exact) qui pourraient limiter les résultats.
Reste en français. Ne réponds pas à la question.
Retourne seulement la requête élargie, sans guillemets ni explication."""

SOURCE_SUMMARY_PROMPT = """Tu résumes des sources documentaires pour AI Riviera, une phrase par source.
Pour chaque source fournie (titre + extrait), écris UNE SEULE phrase concise en français qui dit de quoi parle ce document.
Reste factuel et base-toi uniquement sur l'extrait fourni: n'invente rien, ne suppose rien qui n'y figure pas.

Retourne uniquement un objet JSON associant l'identifiant de chaque source à sa phrase, par exemple:
{"1": "Ce postulat demande la création de jobs d'été pour les jeunes.", "2": "Cette interpellation questionne la sécurité du quai Roussy."}
Ne retourne aucun texte hors JSON."""

REVISION_INSTRUCTION_TEMPLATE = """Voici une réponse que tu as rédigée, et des affirmations qu'elle contient qui ne sont pas confirmées par les extraits sources:
{claims}

Réécris la réponse en retirant ou en qualifiant clairement ces affirmations non confirmées (par exemple en indiquant que l'information n'est pas disponible dans les sources). Garde tout le reste de la réponse intact. Réponds uniquement avec la réponse corrigée, sans commentaire sur la correction elle-même."""


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


def _context_with_extra(results: list[dict], extra_context: str) -> str:
    context_block = build_context(results)
    if extra_context:
        return f"{extra_context}\n\n---\n\n{context_block}"
    return context_block


def answer_with_openai(question: str, results: list[dict], extra_context: str = "") -> str | None:
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
                        f"Extraits disponibles:\n{_context_with_extra(results, extra_context)}"
                    ),
                },
            ],
        )
        return response.output_text
    except Exception as exc:
        return f"Je n'ai pas pu appeler OpenAI pour générer une synthèse: {exc}"


def answer_with_mistral(question: str, results: list[dict], extra_context: str = "") -> str | None:
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
                            f"Extraits disponibles:\n{_context_with_extra(results, extra_context)}"
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


def short_openai_completion(
    system_prompt: str,
    user_content: str,
    model_env: str,
    max_tokens: int = 120,
    default_model: str = "gpt-4.1-mini",
    timeout: float = 20,
) -> str | None:
    api_key = get_secret("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=timeout)
        response = client.responses.create(
            model=get_secret(model_env, default_model),
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_output_tokens=max_tokens,
        )
        content = response.output_text.strip()
        return content or None
    except Exception:
        return None


def short_mistral_completion(
    system_prompt: str,
    user_content: str,
    model_env: str,
    max_tokens: int = 120,
    default_model: str = "mistral-small-latest",
    timeout: float = 20,
) -> str | None:
    api_key = get_secret("MISTRAL_API_KEY")
    if not api_key:
        return None

    try:
        model = get_secret(model_env, default_model)
        response = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        return content or None
    except Exception:
        return None


def rewrite_query_with_openai(question: str) -> str | None:
    return short_openai_completion(QUERY_REWRITE_PROMPT, question, "OPENAI_REWRITE_MODEL", max_tokens=120)


def rewrite_query_with_mistral(question: str) -> str | None:
    return short_mistral_completion(QUERY_REWRITE_PROMPT, question, "MISTRAL_REWRITE_MODEL", max_tokens=120)


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


def _llm_completion(
    system_prompt: str,
    user_content: str,
    mistral_model_env: str,
    openai_model_env: str,
    max_tokens: int = 180,
    default_mistral_model: str = "mistral-small-latest",
    default_openai_model: str = "gpt-4.1-mini",
    timeout: float = 20,
) -> str | None:
    """Shared provider-fallback call for the short, structured LLM steps (classify, verify, broaden)."""
    provider = get_secret("LLM_PROVIDER", "auto").lower().strip()
    if provider in {"none", "off", "extracts"}:
        return None
    kwargs_mistral = dict(max_tokens=max_tokens, default_model=default_mistral_model, timeout=timeout)
    kwargs_openai = dict(max_tokens=max_tokens, default_model=default_openai_model, timeout=timeout)
    if provider == "mistral":
        return short_mistral_completion(system_prompt, user_content, mistral_model_env, **kwargs_mistral)
    if provider == "openai":
        return short_openai_completion(system_prompt, user_content, openai_model_env, **kwargs_openai)
    return (
        short_mistral_completion(system_prompt, user_content, mistral_model_env, **kwargs_mistral)
        or short_openai_completion(system_prompt, user_content, openai_model_env, **kwargs_openai)
    )


def _parse_json_object(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def broaden_query_with_llm(question: str) -> str | None:
    content = _llm_completion(BROADEN_QUERY_PROMPT, question, "MISTRAL_REWRITE_MODEL", "OPENAI_REWRITE_MODEL", max_tokens=120)
    if not content:
        return None
    content = " ".join(content.split())
    if not content or len(content) > 500:
        return None
    return content


def classify_question_with_llm(question: str) -> dict:
    default = {"complexity": "simple", "mode": "single", "subqueries": []}
    content = _llm_completion(CLASSIFY_PROMPT, question, "MISTRAL_CLASSIFY_MODEL", "OPENAI_CLASSIFY_MODEL", max_tokens=220)
    if not content:
        return default

    parsed = _parse_json_object(content)
    complexity = parsed.get("complexity") if parsed.get("complexity") in {"simple", "complex"} else "simple"
    mode = parsed.get("mode") if parsed.get("mode") in {"single", "multi"} else "single"

    subqueries = []
    for item in parsed.get("subqueries") or []:
        if isinstance(item, dict) and item.get("query"):
            subqueries.append({"label": str(item.get("label") or "")[:120], "query": str(item["query"])[:300]})

    if mode == "multi" and len(subqueries) < 2:
        mode = "single"
        subqueries = []
        complexity = "simple"

    return {"complexity": complexity, "mode": mode, "subqueries": subqueries}


def summarize_sources_with_llm(grouped_sources: list[dict]) -> dict[str, str]:
    """One short French sentence per source (what it's about), keyed by the
    same 1-based index used when rendering the Sources list. One batched
    call for all sources, not one call per source.
    """
    if not grouped_sources:
        return {}

    payload = {"sources": []}
    for index, source in enumerate(grouped_sources, start=1):
        metadata = source["metadata"]
        title = metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document")
        passages = source.get("passages") or []
        excerpt = ""
        if passages:
            excerpt = fix_mojibake(str(passages[0].get("text") or passages[0].get("content") or "")).strip()
            excerpt = " ".join(excerpt.split())[:600]
        payload["sources"].append({"id": str(index), "title": fix_mojibake(str(title)), "excerpt": excerpt})

    content = _llm_completion(
        SOURCE_SUMMARY_PROMPT,
        json.dumps(payload, ensure_ascii=False),
        "MISTRAL_SUMMARY_MODEL",
        "OPENAI_SUMMARY_MODEL",
        max_tokens=60 * len(grouped_sources) + 100,
    )
    if not content:
        return {}

    parsed = _parse_json_object(content)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value).strip() for key, value in parsed.items() if str(value).strip()}


def verify_answer_against_sources(question: str, answer: str, results: list[dict]) -> list[str]:
    if not results or not answer:
        return []
    user_content = (
        f"Question:\n{question}\n\n"
        f"Réponse à vérifier:\n{answer}\n\n"
        f"Extraits sources:\n{build_context(results)}"
    )
    content = _llm_completion(
        VERIFICATION_PROMPT,
        user_content,
        "MISTRAL_VERIFY_MODEL",
        "OPENAI_VERIFY_MODEL",
        max_tokens=300,
        default_mistral_model="mistral-large-latest",
        default_openai_model="gpt-4.1",
        timeout=35,
    )
    if not content:
        return []
    claims = _parse_json_object(content).get("unsupported_claims")
    if not isinstance(claims, list):
        return []
    return [str(claim).strip() for claim in claims if str(claim).strip()][:8]


def revise_answer_removing_claims(question: str, answer: str, results: list[dict], claims: list[str]) -> str | None:
    claims_block = "\n".join(f"- {claim}" for claim in claims)
    revision_system_prompt = SYSTEM_PROMPT + "\n\n" + REVISION_INSTRUCTION_TEMPLATE.format(claims=claims_block)
    user_content = (
        f"Question:\n{question}\n\n"
        f"Réponse initiale:\n{answer}\n\n"
        f"Extraits disponibles:\n{build_context(results)}"
    )
    return _llm_completion(
        revision_system_prompt,
        user_content,
        "MISTRAL_VERIFY_MODEL",
        "OPENAI_VERIFY_MODEL",
        max_tokens=900,
        default_mistral_model="mistral-large-latest",
        default_openai_model="gpt-4.1",
        timeout=35,
    )


def verification_enabled() -> bool:
    return get_secret("ENABLE_ANSWER_VERIFICATION", "true").lower().strip() in {"1", "true", "yes", "on"}


def verify_and_revise_answer(question: str, answer: str, results: list[dict]) -> tuple[str, list[str]]:
    """Bounded self-check: at most one extra verification call, at most one revision call.

    Returns (final_answer, flagged_claims). flagged_claims is reported even if the
    revision call itself fails, so the caller can still log/surface what was caught.
    """
    if not verification_enabled():
        return answer, []

    claims = verify_answer_against_sources(question, answer, results)
    if not claims:
        return answer, []

    revised = revise_answer_removing_claims(question, answer, results, claims)
    if revised:
        return fix_mojibake(revised), claims
    return answer, claims


def result_rerank_candidate(result: dict, candidate_id: str) -> dict:
    metadata = result.get("metadata") or {}
    title = metadata.get("filename") or metadata.get("title") or result.get("title") or result.get("relative_text_path", "")
    year = metadata.get("year") or metadata.get("date") or result.get("date") or ""
    category = metadata.get("category") or metadata.get("doc_type") or result.get("doc_type") or ""
    source_kind = ""
    if metadata.get("canonical_object") is True:
        source_kind = "source canonique"
    elif metadata.get("canonical_object") is False:
        source_kind = "document lié"
    text = fix_mojibake(str(result.get("text") or result.get("content") or "")).strip()
    text = " ".join(text.split())[:900]
    return {
        "id": candidate_id,
        "title": fix_mojibake(str(title)),
        "year": str(year),
        "category": str(category),
        "source_kind": source_kind,
        "excerpt": text,
    }


def parse_rerank_ids(content: str, allowed_ids: set[str]) -> list[str]:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    start = content.find("[")
    end = content.rfind("]")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    try:
        raw_ids = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_ids, list):
        return []
    ids = []
    for raw_id in raw_ids:
        candidate_id = str(raw_id).strip()
        if candidate_id in allowed_ids and candidate_id not in ids:
            ids.append(candidate_id)
    return ids


def rerank_results_with_llm(
    question: str,
    results: list[dict],
    keep: int = 20,
    max_candidates: int = 30,
) -> list[dict]:
    if len(results) <= 1:
        return results

    provider = get_secret("LLM_PROVIDER", "auto").lower().strip()
    if provider in {"none", "off", "extracts"}:
        return results[:keep]

    candidates = results[:max_candidates]
    candidate_by_id = {str(index): result for index, result in enumerate(candidates, start=1)}
    payload = {
        "question": question,
        "candidates": [
            result_rerank_candidate(result, candidate_id)
            for candidate_id, result in candidate_by_id.items()
        ],
    }
    user_content = json.dumps(payload, ensure_ascii=False)

    if provider == "mistral":
        content = short_mistral_completion(LLM_RERANK_PROMPT, user_content, "MISTRAL_RERANK_MODEL", max_tokens=180)
    elif provider == "openai":
        content = short_openai_completion(LLM_RERANK_PROMPT, user_content, "OPENAI_RERANK_MODEL", max_tokens=180)
    else:
        content = (
            short_mistral_completion(LLM_RERANK_PROMPT, user_content, "MISTRAL_RERANK_MODEL", max_tokens=180)
            or short_openai_completion(LLM_RERANK_PROMPT, user_content, "OPENAI_RERANK_MODEL", max_tokens=180)
        )
    if not content:
        return results[:keep]

    selected_ids = parse_rerank_ids(content, set(candidate_by_id))
    if not selected_ids:
        return results[:keep]

    selected = [candidate_by_id[candidate_id] for candidate_id in selected_ids]
    selected_keys = {id(result) for result in selected}
    for result in results:
        if len(selected) >= keep:
            break
        if id(result) not in selected_keys:
            selected.append(result)
            selected_keys.add(id(result))
    return selected[:keep]


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


def answer_with_llm(question: str, results: list[dict], extra_context: str = "") -> str | None:
    provider = get_secret("LLM_PROVIDER", "auto").lower().strip()

    if provider == "mistral":
        return answer_with_mistral(question, results, extra_context)
    if provider == "openai":
        return answer_with_openai(question, results, extra_context)
    if provider in {"none", "off", "extracts"}:
        return None

    if get_secret("MISTRAL_API_KEY"):
        return answer_with_mistral(question, results, extra_context)
    return answer_with_openai(question, results, extra_context)


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


def answer_from_sources(question: str, results: list[dict], extra_context: str = "") -> str:
    ai_answer = answer_with_llm(question, results, extra_context)
    if ai_answer:
        return fix_mojibake(ai_answer)

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
    return "\n".join(lines)
