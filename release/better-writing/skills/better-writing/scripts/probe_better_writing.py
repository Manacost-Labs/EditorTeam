#!/usr/bin/env python3
"""Route genuine prose work to the smallest useful better-writing references.

Usage:
    python3 probe_better_writing.py --prompt "..."
    python3 probe_better_writing.py --suite
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Sequence


WRITING_VERIFICATION = r"\bverif(?:y|ying)\b.{0,80}\b(?:rewrite|rewritten|revision|revised|edited|draft|prose|copy)\b"
RUSSIAN_TEXT = r"[а-яё]"

PROSE_ACTIONS = (
    r"\b(?:adapt(?:ing)?|diagnos(?:e|ing)|draft(?:ing)?|write|updat(?:e|ing)|rewrit(?:e|ing)|revis(?:e|ing)|edit(?:ed|ing)?|review(?:ing)?|polish(?:ing)?|tighten(?:ing)?|improv(?:e|ing)|clarif(?:y|ying)|human(?:ise|ize|ising|izing)|de-robot(?:ise|ize)|de-ai|de-bot|rephras(?:e|ing)|replac(?:e|ing)|remov(?:e|ing)|strip(?:ping)?|ban(?:ned|ning|s)?|recast(?:ing)?|limit(?:ing)?|avoid(?:ed|ing|s)?|standardis(?:e|ing)|standardiz(?:e|ing))\b",
    r"\bmake\b.{0,80}\b(?:clearer|warmer|more\s+human|less\s+(?:stiff|generic|robotic))\b",
    WRITING_VERIFICATION,
    r"\b(?:genre|structure|format)\b",
    r"\b(?:адаптир(?:уй|овать)|напиши|обнови|актуализируй|сделай|создай|собери|подготовь|синтезируй|перепиш(?:и|ите)|переработай|отредактир(?:уй|уйте|овать|ованный)|редактир(?:уй|овать)|исправь|улучш(?:и|ить)|проясни|сократи|вычитай|убери|удали|замени|очеловечь|проведи\s+(?:редакторский\s+)?разбор|покажи\b.{0,80}\bотредактированный)\b",
    r"\b(?:редактур(?:а|у|ы|ой|е)|редактирование|переписывание|рерайт|вычитка|редакторский\s+разбор)\b",
)
PROSE_SCOPE = (
    r"\b(?:prose|copy|microcopy|sentence|paragraph|comment|document|draft|blurb|message|reply|response|explanation|intro(?:duction)?|outro|heading|voice|tone|cadence|style|wording|phrasing|diction|language|words?|phrases?|tropes?|clich[eé]s?|tics?|mannerisms?|tells?|patterns?|slogan|tagline|source\s+pack|project\s+profile|claim\s+ledger|evidence\s+ledger)\b",
    r"\b(?:announcement|notice|note|essay|poem|story|fiction|memo|email|cover\s+letter|newsletter|article|report|brief|proposal|policy|statement|post|caption|bio|release\s+note|product\s+spec|pull\s+request|landing[- ]page|launch[- ]page|homepage|pricing\s+page)\b",
    r"\b(?:readme|guide|tutorial|how-to|runbook|walkthrough|documentation|docs|ui)\s+(?:draft|intro|section|copy|text|page)?\b",
    WRITING_VERIFICATION,
    r"\b(?:текст(?:а|у|ом|е)?|проз(?:а|у|ы|ой|е)|копирайт|микрокопи|предложени(?:е|я|ю|ем)|абзац(?:а|у|ем|е)?|черновик(?:а|у|ом|е)?|сообщени(?:е|я|ю|ем)|ответ(?:а|у|ом|е)?|объяснени(?:е|я|ю|ем)|введени(?:е|я|ю|ем)|заголов(?:ок|ка|ку|ком)|тон(?:а|у|ом|е)?|ритм(?:а|у|ом|е)?|стил(?:ь|я|ю|ем)|формулировк(?:а|и|у|ой)|слов(?:о|а|у|ом)|фраз(?:а|ы|у|ой)|клише|слоган(?:а|у|ом|е)?|pdf|пдф|скан(?:а|ов|ы)?|ocr|распознанн(?:ый|ого|ые)\s+текст|корпус(?:а|у|ом)?|массив(?:а|у|ом)?\s+(?:документов|данных)|профил(?:ь|я|ю|ем)\s+проекта|пакет\s+источников|реестр\s+(?:утверждений|доказательств))\b",
    r"\b(?:анонс|уведомление|заметка|эссе|письмо|рассылка|статья|отч[её]т|записка|предложение|политика|пост|подпись|биография|релиз|спецификация|лендинг|инструкция|руководство|документация)\b",
)
NEGATED_PROSE_CLAUSES = (
    r"\b(?:do\s+not|don't)\s+(?:(?:write|rewrite|edit|improve|revise|polish|humanise|humanize|touch)\b(?:\s+or\s+)?){1,3}[^.;\n]*",
    r"\bthere\s+is\s+no\s+prose\b[^.;\n]*",
    r"\b(?:не|ничего\s+не)\s+(?:переписывай|редактируй|улучшай|исправляй|трогай)\b[^.;\n]*",
    r"\bбез\s+(?:редактуры|редактирования|переписывания|редакторского\s+текста)\b[^.;\n]*",
)
CODE_ONLY = (
    r"\b(?:debug|compile|build|deploy|implement|refactor|fix)\b.*\b(?:code|function|class|api|test|error|exception|stack trace|hydration|typescript|python|react|next\.js)\b",
    r"\b(?:why|how)\s+(?:does|do|can)\b.*\b(?:code|function|api|test|error|exception)\b",
    r"\b(?:replac(?:e|ing)|remov(?:e|ing)|recast(?:ing)?|limit(?:ing)?|avoid(?:ed|ing|s)?|standardis(?:e|ing)|standardiz(?:e|ing))\b.*\b(?:colons?|semi-?colons?|dashes|punctuation)\b.*\b(?:code|syntax|typescript|javascript|python|yaml|json|css|regex)\b",
    r"\b(?:rewrite|rephrase|rename|replace|remove|strip|ban|avoid)\b.*\b(?:identifiers?|selectors?|class\s+names?|variable\s+names?|function\s+names?|type\s+names?|schema\s+keys?|configuration\s+keys?)\b",
    r"\b(?:rewrite|rephrase|rename|replace|remove|strip|ban|avoid|humanise|humanize)\b.*\b(?:source\s+(?:code|file)|codebase|python\s+file|typescript\s+file|javascript\s+file|tsx\s+file|jsx\s+file)\b",
    r"\b(?:исправь|отладь|реализуй|перепиши|отрефакторь)\b.*\b(?:код|функци(?:ю|и)|класс|api|тест|ошибк(?:у|и)|исключение|typescript|python|react)\b",
    r"\b(?:почему|как)\b.*\b(?:код|функци(?:я|и)|api|тест|ошибк(?:а|и))\b",
)
MIXED_PROSE_SCOPE = (
    r"\b(?:prose|copy|microcopy|message|reply|response|error\s+message|notification|ui\s+copy|user-facing\s+text|explanation|documentation|docs|readme|guide|tutorial|article|report|memo|email)\b",
    r"\b(?:текст|проза|микрокопи|сообщение|ответ|уведомление|пользовательский\s+текст|объяснение|документация|инструкция|статья|отч[её]т|письмо)\b",
)
AUTHORSHIP_CLASSIFICATION = (
    r"\b(?:assess|classify|detect|determine|judge|review|verify)\b.{0,100}\b(?:authorship|written|authored|generated)\b.{0,60}\b(?:ai|bot|human|llm|model)\b",
    r"\b(?:was|were|is)\b.{0,80}\bwritten\s+by\b.{0,30}\b(?:ai|a\s+bot|a\s+human|an\s+llm|a\s+model)\b",
    r"\b(?:определи|проверь|выясни)\b.{0,100}\b(?:написал|создал|сгенерировал)\b.{0,60}\b(?:человек|бот|ии|нейросеть|модель)\b",
    r"\b(?:человек|бот|ии|нейросеть|модель)\b.{0,60}\b(?:написал|создал|сгенерировал)\b",
)
FACT_CHECK_ONLY = (
    r"\b(?:fact[ -]?check|verify|validate|confirm)\b.*\b(?:fact|claim|source|citation|accuracy|true)\b",
    r"\b(?:is|are|was|were|does|do)\b.*\b(?:true|accurate|correct)\b",
    r"\b(?:проверь|подтверди|установи|выясни)\b.*\b(?:факт|утверждение|источник|цитат(?:у|ы)|точность|правда|верно)\b",
    r"\b(?:правда\s+ли|верно\s+ли|точно\s+ли)\b",
)
DOCS_QUESTION = (
    r"\b(?:where|how)\s+(?:is|are|do i find|can i find)\b.*\b(?:docs|documentation|readme)\b",
    r"\b(?:what does|how does)\b.*\b(?:the docs|documentation|readme)\b",
    r"\b(?:что\s+(?:означает|делает)|как\s+работает|где\s+найти)\b.*\b(?:документаци(?:я|и)|readme|параметр|endpoint|api)\b",
)
PUNCTUATION_TRANSFORM = (
    r"\b(?:replac(?:e|ing)|remov(?:e|ing)|recast(?:ing)?|limit(?:ing)?|standardis(?:e|ing)|standardiz(?:e|ing))\b.{0,80}\b(?:em[ -]?dash(?:es)?|semi-?colons?|colons?|punctuation)\b",
    r"\b(?:em[ -]?dash(?:es)?|semi-?colons?|colons?|punctuation)\b.{0,80}\b(?:replac(?:e|ing)|remov(?:e|ing)|recast(?:ing)?|limit(?:ing)?|standardis(?:e|ing)|standardiz(?:e|ing))\b",
    r"\b(?:dash|semi-?colon|colon|punctuation)[ -]?(?:heavy|dense|awkward|overused)\b",
    r"\b(?:ban(?:ned|ning|s)?|avoid(?:ed|ing|s)?)\b.{0,80}\b(?:em[ -]?dash(?:es)?|semi-?colons?|colons|punctuation)\b",
    r"\b(?:em[ -]?dash(?:es)?|semi-?colons?|colons|punctuation)\b.{0,80}\b(?:ban(?:ned|ning|s)?|avoid(?:ed|ing|s)?)\b",
    r"\b(?:исправь|замени|убери|сократи|ограничь)\b.{0,80}\b(?:тире|дефис(?:ы)?|двоеточи(?:е|я)|точк(?:у|и)\s+с\s+запятой|пунктуаци(?:ю|я))\b",
    r"\b(?:тире|двоеточи(?:е|я)|точк(?:а|и)\s+с\s+запятой|пунктуаци(?:я|ю))\b.{0,80}\b(?:исправь|замени|убери|сократи|механически)\b",
)
STRUCTURE_AND_DIGESTIBILITY = (
    r"\b(?:digestible|scannable|wall\s+of\s+text|dense\s+(?:paragraph|passage|prose)|overloaded\s+paragraph|long\s+(?:paragraph|passage|prose))\b",
    r"\b(?:break|split|chunk)\b.{0,60}\b(?:paragraph|passage|prose|text)\b",
    r"\b(?:restructur(?:e|ing)|reorgani[sz](?:e|ing)|reorder(?:ing)?)\b.{0,80}\b(?:paragraph|passage|prose|article|memo|report|guide|draft)\b",
    r"\b(?:natural|clearer|better)\s+(?:structure|flow)\b",
    r"\b(?:easier|easy)\s+to\s+scan\b",
    r"\b(?:плотный|перегруженный|длинный)\s+(?:абзац|текст|отч[её]т)\b",
    r"\b(?:разбей|раздели|перестрой|структурируй|реорганизуй)\b.{0,80}\b(?:абзац|текст|статью|отч[её]т|инструкцию|черновик)\b",
    r"\b(?:естественн(?:ая|ый|ое)|понятн(?:ая|ый|ое)|лучш(?:ая|ий|ее))\s+(?:структура|подача|ход)\b",
)
FORMULAIC_LANGUAGE = (
    r"\bformulaic[- ]language\b",
    r"\b(?:ai|llm|chatgpt|chatbot|bot|model)[- ]?(?:sounding|generated|written|like)?\s*(?:words?|phrases?|diction|wording|language|prose|copy|tropes?|clich[eé]s?|tics?|mannerisms?|tells?|patterns?|isms?)\b",
    r"\b(?:formulaic|template[- ]?like|assistant[- ]?like|bot[- ]?like|llm[- ]?like|chatgpt[- ]?ish|model[- ]?sounding|machine[- ]?smooth)\s+(?:words?|phrases?|diction|wording|language|prose|copy|tropes?|clich[eé]s?|tics?|mannerisms?|tells?|patterns?)\b",
    r"\b(?:canned|stock|scripted|prefab(?:ricated)?)\b.{0,60}\b(?:phrases?|frames?|wording|language|tropes?|clich[eé]s?|tics?|patterns?|boilerplate|hooks?|openers?|openings?|closers?|closings?|empathy|validation)\b",
    r"\b(?:academic|research|support|customer[- ]service)\s+boilerplate\b",
    r"\b(?:(?:bridg(?:e|es|ed|ing)|clos(?:e|es|ed|ing)|fill(?:s|ed|ing)?)\s+(?:a\s+|the\s+)?gap|marks?\s+(?:a\s+)?(?:significant\s+|pivotal\s+|major\s+)?shift|here(?:'|’)s\s+(?:the\s+)?kicker|plot\s+twist|in\s+(?:conclusion|summary)|only\s+time\s+will\s+tell|paves?\s+the\s+way|plays?\s+(?:a\s+)?(?:key|critical|pivotal)\s+role|at\s+its\s+core)\b",
    r"\b(?:chatgpt[- ]?isms?|ai[- ]?isms?|llm[- ]?isms?|de-ai|de-bot)\b",
    r"\b(?:канцелярит|формульн(?:ый|ая|ое)|шаблонн(?:ый|ая|ое)|нейросетев(?:ый|ая|ое)|ии[- ]?(?:текст|стиль|клише)|рекламн(?:ые|ый)\s+клише|машинн(?:ый|ая)\s+(?:стиль|подача))\b",
    r"\b(?:играет\s+ключевую\s+роль|открывает\s+новые\s+возможности|выводит\s+на\s+новый\s+уровень|на\s+сегодняшний\s+день|в\s+заключение)\b",
)

PDF_AND_LARGE_CORPORA = (
    r"\b(?:pdfs?|scanned?\s+(?:pdfs?|documents?|pages?)|ocr(?:ed)?\s+(?:text|output|documents?)|document\s+corpus|source\s+corpus|hundreds?\s+of\s+(?:files|documents|pdfs?)|large\s+(?:document|source|text)\s+(?:set|collection|corpus)|batch\s+(?:of\s+)?(?:files|documents|pdfs?))\b",
    r"\b(?:pdf|пдф|скан(?:ы|ов|а)?|ocr|распознанн(?:ый|ого|ые)\s+текст|корпус(?:а|у|ом)?\s+(?:документов|текстов|источников)|сот(?:ня|ни|ен)\s+(?:файлов|документов|pdf|пдф)|больш(?:ой|ого|им)\s+(?:массив(?:ом|а)?|объ[её]м(?:ом|а)?)\s+(?:документов|текстов|источников|данных)|пакет(?:ом|ная\s+обработка)\s+(?:файлов|документов|pdf|пдф))\b",
    r"\b(?:page[- ]level\s+provenance|source\s+ledger|claim\s+ledger|source\s+manifest|страничн(?:ая|ую)\s+привязк(?:а|у)|реестр\s+источников|манифест\s+(?:файлов|источников))\b",
)

PROJECT_PROFILE = (
    r"\b(?:project\s+profile|house[ -]style\s+profile|audience\s+profile|project\s+terminology|approved\s+facts?|project\s+voice\s+rules?)\b",
    r"\b(?:профил(?:ь|я|ю|ем)\s+проекта|терминологи(?:я|и|ю)\s+проекта|глоссари(?:й|я|ю)|утвержд[её]нн(?:ые|ых)\s+факт(?:ы|ов)|голос\s+проекта|аудитори(?:я|и|ю)\s+проекта)\b",
)

SOURCE_PACK_AND_CLAIMS = (
    r"\b(?:source\s+pack|source\s+package|claim\s+ledger|evidence\s+ledger|claim\s+audit|claim\s+coverage|provenance\s+audit|incremental\s+(?:corpus|source\s+update))\b",
    r"\b(?:пакет\s+источников|реест\s+(?:утверждений|доказательств)|аудит\s+утверждений|покрытие\s+утверждений|аудит\s+происхождения|инкрементальн(?:ое|ый)\s+обновление)\b",
)


@dataclass(frozen=True)
class RouteRule:
    reference: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class TestCase:
    name: str
    prompt: str
    expect: tuple[str, ...]
    forbid: tuple[str, ...] = ()


ROUTE_RULES = (
    RouteRule("references/operating-contract.md", (r"\b(?:adapt(?:ing)?|diagnos(?:e|ing)|draft(?:ing)?|rewrit(?:e|ing)|revis(?:e|ing)|edit(?:ed|ing)?|review(?:ing)?|human(?:ise|ize|ising|izing)|verif(?:y|ying))\b", r"\b(?:напиши|перепиш(?:и|ите)|переработай|отредактир(?:уй|уйте)|редактир(?:уй|ование)|улучши|вычитай|разбор|редактур(?:а|у|ы|ой|е))\b")),
    RouteRule("references/revision-pass-stack.md", (r"\b(?:adapt(?:ing)?|recast(?:ing)?|rewrit(?:e|ing)|rewritten|revis(?:e|ing)|revised|edit(?:ed|ing)?|polish(?:ing)?|tighten(?:ing)?|improv(?:e|ing)|cleanup|revision|draft(?:ing)?)\b", r"\b(?:перепиш(?:и|ите)|переработай|отредактир(?:уй|уйте)|редактирование|редактур(?:а|у|ы|ой|е)|улучши|вычитай)\b")),
    RouteRule("references/natural-structure-and-digestibility.md", STRUCTURE_AND_DIGESTIBILITY),
    RouteRule("references/pdf-and-large-corpora.md", PDF_AND_LARGE_CORPORA),
    RouteRule("references/project-profiles.md", PROJECT_PROFILE),
    RouteRule("references/source-packs-and-claim-audits.md", SOURCE_PACK_AND_CLAIMS),
    RouteRule("references/foundations.md", (r"\b(?:clear|clarity|concise|grammar|usage|composition|basics?)\b", r"\b(?:ясн(?:ость|ее)|понятн(?:ее|ый)|кратк(?:о|ий)|грамматик(?:а|у)|вычитка)\b")),
    RouteRule("references/voice-and-rhythm.md", (r"\b(?:stiff|flat|bloodless|formal|robotic|cadence|rhythm|voice|owned|more\s+human|hedg(?:e|ing)|awkward|clipped)\b", r"\b(?:сухой|безжизненный|формальный|роботизированный|ритм|голос|естественн(?:ее|ый)|неуклюжий|рубленый)\b")),
    RouteRule("references/punctuation-and-sentence-flow.md", PUNCTUATION_TRANSFORM),
    RouteRule("references/genericity-and-stiffness.md", (r"\b(?:generic|corporate|canned|fluffy|buzzwords?|over-signposted|dramatic|marketing[- ]speak|ceremonial)\b", r"\b(?:канцелярит|шаблонн(?:ый|ая)|корпоративн(?:ый|ая)|рекламн(?:ый|ая|ые)|клише|лозунг(?:и)?)\b")),
    RouteRule(
        "references/ai-isms-and-humanisation.md",
        (r"\b(?:human(?:ise|ize)|ai[- ]?isms?|sound\s+(?:more\s+)?human|less\s+(?:robotic|ai)|machine[- ]?written)\b", r"\b(?:очеловечь|сделай\s+человечн(?:ее|ым)|убери\s+(?:ии|нейросетевый|машинный)|формульн(?:ая|ый)\s+подача)\b", *FORMULAIC_LANGUAGE),
    ),
    RouteRule(
        "references/formulaic-language-catalogue.md",
        (*FORMULAIC_LANGUAGE, r"\b(?:human(?:ise|ize)|ai[- ]?isms?|sound\s+(?:more\s+)?human|less\s+(?:robotic|ai)|machine[- ]?written)\b", r"\b(?:очеловечь|сделай\s+человечн(?:ее|ым)|формульн(?:ая|ый)\s+подача)\b"),
    ),
    RouteRule(
        "references/style-bundles.md",
        (
            r"\b(?:style|tone|publication|house[ -](?:style|voice)|voice\s+(?:sample|sheet|family)|operator\s+voice|newsletter\s+voice|editorial\s+voice|technical[ -]teacher\s+voice)\b",
            r"\b(?:simon\s+willison|julia\s+evans|gergely|lenny|reuters|bloomberg|paul\s+graham)\b",
        ),
    ),
    RouteRule("references/genre-modes.md", (r"\b(?:announcement|notice|guide|tutorial|how-to|docs?|readme|runbook|memo|brief|report|essay|article|landing[- ]page|launch[- ]page|homepage|pricing\s+page|email|walkthrough)\b", r"\b(?:анонс(?:а|у|ом|е)?|уведомлени(?:е|я|ю)|инструкци(?:я|ю|и)|руководств(?:о|а|у)|документаци(?:я|ю|и)|отч[её]т(?:а|у|ом|е)?|записк(?:а|у|и)|эссе|стать(?:я|ю|и)|лендинг(?:а|у|ом|е)?|письм(?:о|а|у))\b")),
    RouteRule("references/russian-profile.md", (RUSSIAN_TEXT, r"\b(?:russian|русск(?:ий|ая|ое|ую))\b")),
    RouteRule(
        "references/quality-gates.md",
        (r"\b(?:final\s+(?:review|pass|check)|ready\s+to\s+publish|quality\s+check|sign[- ]?off|review[- ]only)\b", WRITING_VERIFICATION, r"\b(?:финальн(?:ая|ый)\s+проверка|готов(?:о|ый)\s+к\s+публикации|проверь\s+сохранность|только\s+редакторский\s+разбор)\b"),
    ),
    RouteRule("references/gotchas.md", (r"\b(?:over-edit(?:ed|ing)?|already\s+been\s+edited|edited\s+(?:two|three|multiple)\s+times|getting\s+worse|each\s+pass|too\s+polished|modes?\s+conflict|conflicting\s+(?:genres?|modes?)|can'?t\s+get\s+this\s+right)\b",)),
)


TEST_CASES = (
    TestCase(
        "humanise_mixed_docs_scope",
        "Humanise the prose in this React README introduction without changing the code example.",
        (
            "references/operating-contract.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
            "references/genre-modes.md",
        ),
        ("references/punctuation-and-sentence-flow.md",),
    ),
    TestCase(
        "ban_formulaic_ai_diction",
        "Remove AI-sounding words and canned phrases like bridge the gap and marks a significant shift from this report; rephrase them from what actually changed.",
        (
            "references/operating-contract.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
            "references/genericity-and-stiffness.md",
            "references/genre-modes.md",
        ),
    ),
    TestCase(
        "ban_broad_ai_tropes",
        "Strip ChatGPTisms, canned hooks, AI tropes, and model-sounding phrasing from this article.",
        (
            "references/operating-contract.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
        ),
    ),
    TestCase(
        "rewrite_academic_boilerplate",
        "Rewrite this research summary and replace its stock research-purpose, significance, and future-work phrases.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
        ),
    ),
    TestCase(
        "rewrite_scripted_support_reply",
        "Rewrite this support reply to remove scripted empathy, blanket validation, and chatbot closing residue.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
        ),
    ),
    TestCase(
        "avoid_named_formula_in_release_note",
        "Write a terse release note and avoid phrases like bridge the gap.",
        (
            "references/operating-contract.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
        ),
    ),
    TestCase(
        "remove_named_formula_from_policy",
        "Edit this policy: remove 'in conclusion' and end on the final supported requirement.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
        ),
    ),
    TestCase(
        "humanise_technical_gap_and_shift_prose",
        "Humanise the prose in this technical glossary entry, but preserve the exact terms phase shift, distribution shift, and band gap.",
        (
            "references/operating-contract.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
        ),
    ),
    TestCase(
        "stiff_runbook_intro",
        "Review and rewrite this runbook intro. It is clear but still stiff and corporate.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/voice-and-rhythm.md",
            "references/genericity-and-stiffness.md",
            "references/genre-modes.md",
        ),
        ("references/punctuation-and-sentence-flow.md",),
    ),
    TestCase(
        "genre_draft",
        "Draft a concise launch email for existing customers, then give it a final quality check.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/genre-modes.md",
            "references/quality-gates.md",
        ),
    ),
    TestCase(
        "genre_question",
        "What genre and structure should this reflective essay use before I start drafting?",
        ("references/operating-contract.md", "references/genre-modes.md"),
        ("references/style-bundles.md",),
    ),
    TestCase(
        "explicit_publication_style",
        "Rewrite this report in a Reuters-style, fact-first tone without copying signature phrasing.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/style-bundles.md",
            "references/genre-modes.md",
        ),
    ),
    TestCase(
        "slogan_draft",
        "Draft a new product slogan from scratch, but ask for the minimum audience context first.",
        ("references/operating-contract.md", "references/revision-pass-stack.md"),
    ),
    TestCase(
        "replace_em_dashes_by_relation",
        "Rewrite these sentences to replace the em dashes with natural punctuation and sentence structures; use semicolons and colons only where their grammar and relation fit.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/punctuation-and-sentence-flow.md",
        ),
    ),
    TestCase(
        "repair_punctuation_heavy_prose",
        "Edit this punctuation-heavy paragraph so the flow is less awkward without changing its meaning.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/voice-and-rhythm.md",
            "references/punctuation-and-sentence-flow.md",
        ),
    ),
    TestCase(
        "restructure_dense_analysis",
        "Rewrite this long, dense analysis into digestible prose without turning every sentence into a bullet.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/natural-structure-and-digestibility.md",
        ),
        ("references/punctuation-and-sentence-flow.md",),
    ),
    TestCase(
        "eval_2_exact_memo_route",
        "Rewrite the attached Q3 operations memo for an executive audience. Make the argument easier to scan and less corporate, but preserve 18.4%, Q3, the sentence `We have not tested this in production.`, the quoted sentence, and the uncertainty word `may`. Do not add a source, result, or personal experience.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/natural-structure-and-digestibility.md",
            "references/genericity-and-stiffness.md",
            "references/genre-modes.md",
        ),
        (
            "references/style-bundles.md",
            "references/voice-and-rhythm.md",
            "references/research-notes.md",
        ),
    ),
    TestCase(
        "eval_20_exact_dense_analysis_route",
        "Rewrite the attached renewal-pilot analysis for product and engineering readers. Make it digestible by separating the finding, evidence limit, operational consequence, and recommendation where their jobs change. Preserve every number, both uses of `may`, `awaiting_confirmation`, the quoted sentence, and the unresolved callback cause. Do not turn every sentence into its own paragraph, use bullets unless the items are genuine peers, or publish diagnostic labels as headings in this short update.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/natural-structure-and-digestibility.md",
        ),
        (
            "references/quality-gates.md",
            "references/punctuation-and-sentence-flow.md",
            "references/research-notes.md",
        ),
    ),
    TestCase(
        "eval_21_exact_dense_runbook_route",
        "Rewrite the attached long paragraph about staging key rotation as a compact runbook. Use numbered steps for meaningful checkpoints, not every small verb; make the `issuer mismatch` stop condition unmistakable and end with verification. Preserve every command, `kid`, `staging.env`, `issuer mismatch`, `test-user`, and `<old-kid>` exactly. Do not invent a rollback command or claim the rotation succeeded.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/natural-structure-and-digestibility.md",
            "references/genre-modes.md",
        ),
        ("references/research-notes.md",),
    ),
    TestCase("code_only_rewrite", "Rewrite this Python function to remove a race condition.", (), ("references/operating-contract.md",)),
    TestCase(
        "code_identifier_renaming",
        "Rewrite these identifiers to remove formulaic wording.",
        (),
        ("references/operating-contract.md", "references/formulaic-language-catalogue.md"),
    ),
    TestCase(
        "code_source_without_prose",
        "Remove AI language from this Python source file but do not touch prose.",
        (),
        ("references/operating-contract.md", "references/formulaic-language-catalogue.md"),
    ),
    TestCase(
        "code_punctuation_replacement",
        "Replace the colons in this YAML syntax with equals signs.",
        (),
        ("references/punctuation-and-sentence-flow.md",),
    ),
    TestCase(
        "code_em_dash_replacement",
        "Remove the em dashes from these TypeScript comments and return the code patch only.",
        (),
        ("references/punctuation-and-sentence-flow.md",),
    ),
    TestCase(
        "schedule_shift_not_writing",
        "Move Alex from the day shift to the night shift in the schedule.",
        (),
        ("references/formulaic-language-catalogue.md",),
    ),
    TestCase(
        "research_gap_not_rewriting",
        "Find studies addressing this research gap and summarize the evidence; do not revise prose.",
        (),
        ("references/formulaic-language-catalogue.md",),
    ),
    TestCase(
        "colon_topic_not_punctuation",
        "Draft a plain-language article about colon cancer screening.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/genre-modes.md",
        ),
        ("references/punctuation-and-sentence-flow.md",),
    ),
    TestCase("fact_check_only", "Fact-check whether this claim about GDP is accurate.", (), ("references/foundations.md",)),
    TestCase(
        "authorship_classification",
        "Review whether this AI-generated prose was written by a bot.",
        (),
        ("references/operating-contract.md", "references/ai-isms-and-humanisation.md"),
    ),
    TestCase(
        "verify_report_claims_only",
        "Verify whether this report's claims are accurate; do not rewrite it.",
        (),
        ("references/operating-contract.md", "references/revision-pass-stack.md", "references/genre-modes.md"),
    ),
    TestCase(
        "verify_rewritten_report_preservation",
        "Verify that this rewritten report preserves every number and quote.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/genre-modes.md",
            "references/quality-gates.md",
        ),
        ("references/research-notes.md",),
    ),
    TestCase(
        "russian_minimal_edit",
        "Минимально отредактируй этот русский абзац и сохрани все числа, цитаты и степень уверенности.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/russian-profile.md",
        ),
        (
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
            "references/punctuation-and-sentence-flow.md",
        ),
    ),
    TestCase(
        "russian_deep_report_rewrite",
        "Глубоко перепиши этот плотный отчёт для руководителя, но не меняй факты.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/natural-structure-and-digestibility.md",
            "references/genre-modes.md",
            "references/russian-profile.md",
        ),
    ),
    TestCase(
        "russian_review_only",
        "Проведи только редакторский разбор русского черновика и не пиши заменяющий текст.",
        (
            "references/operating-contract.md",
            "references/russian-profile.md",
            "references/quality-gates.md",
        ),
    ),
    TestCase(
        "russian_formulaic_humanisation",
        "Убери из этого анонса канцелярит и рекламные клише, опираясь только на указанные факты.",
        (
            "references/operating-contract.md",
            "references/genericity-and-stiffness.md",
            "references/ai-isms-and-humanisation.md",
            "references/formulaic-language-catalogue.md",
            "references/genre-modes.md",
            "references/russian-profile.md",
        ),
    ),
    TestCase(
        "russian_punctuation_edit",
        "Исправь пунктуацию в русском тексте, но не заменяй тире механически.",
        (
            "references/operating-contract.md",
            "references/punctuation-and-sentence-flow.md",
            "references/russian-profile.md",
        ),
    ),
    TestCase(
        "russian_pdf_report_synthesis",
        "Сделай из 80 PDF красивый русский отчёт: учти сканы и OCR, сохрани ссылки на страницы и перечисли неразобранные файлы.",
        (
            "references/operating-contract.md",
            "references/pdf-and-large-corpora.md",
            "references/genre-modes.md",
            "references/russian-profile.md",
        ),
    ),
    TestCase(
        "english_scanned_pdf_article",
        "Write a polished article from this batch of scanned PDFs, preserve page-level provenance, and flag unreadable OCR.",
        (
            "references/operating-contract.md",
            "references/pdf-and-large-corpora.md",
            "references/genre-modes.md",
        ),
    ),
    TestCase(
        "pure_pdf_operation",
        "Rotate every page in these PDFs by 90 degrees and merge the files; do not write or revise prose.",
        (),
        ("references/pdf-and-large-corpora.md",),
    ),
    TestCase(
        "pure_data_calculation",
        "Рассчитай среднее, медиану и стандартное отклонение для CSV. Текст и отчёт не пиши.",
        (),
        ("references/pdf-and-large-corpora.md",),
    ),
    TestCase(
        "russian_fact_check_only",
        "Проверь, правда ли запуск сократил задержку на 18,4 %, и найди источники. Текст не редактируй.",
        (),
        ("references/operating-contract.md", "references/russian-profile.md"),
    ),
    TestCase(
        "russian_code_only",
        "Исправь ошибку в функции Python и верни патч без редакторского текста.",
        (),
        ("references/operating-contract.md", "references/russian-profile.md"),
    ),
    TestCase(
        "russian_authorship_classification",
        "Определи, написал ли этот русский текст человек или нейросеть. Не редактируй его.",
        (),
        ("references/ai-isms-and-humanisation.md", "references/russian-profile.md"),
    ),
    TestCase(
        "russian_project_profile_rewrite",
        "Перепиши этот отчёт по профилю проекта: сохрани его терминологию, аудиторию и режим чистовика.",
        (
            "references/operating-contract.md",
            "references/revision-pass-stack.md",
            "references/project-profiles.md",
            "references/genre-modes.md",
            "references/russian-profile.md",
        ),
    ),
    TestCase(
        "english_source_pack_claim_audit",
        "Write a source-backed report from this Source Pack and audit every used claim against its claim ledger.",
        (
            "references/operating-contract.md",
            "references/source-packs-and-claim-audits.md",
            "references/genre-modes.md",
        ),
    ),
    TestCase(
        "russian_incremental_source_pack_update",
        "Обнови статью по изменившемуся Source Pack, повторно проверь реестр утверждений и не переобрабатывай неизменившиеся источники.",
        (
            "references/operating-contract.md",
            "references/source-packs-and-claim-audits.md",
            "references/genre-modes.md",
            "references/russian-profile.md",
        ),
    ),
    TestCase(
        "pure_json_schema_validation",
        "Validate this JSON schema and return JSON only; do not write or revise prose.",
        (),
        ("references/project-profiles.md", "references/source-packs-and-claim-audits.md"),
    ),
    TestCase("docs_question", "Where do I find the API documentation for this option?", (), ("references/genre-modes.md",)),
)


def matches_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def add_unique(items: list[str], new_items: Sequence[str]) -> None:
    for item in new_items:
        if item not in items:
            items.append(item)


def has_prose_request(text: str) -> bool:
    """Accept prose work, including prose embedded in an otherwise technical prompt."""

    positive_text = text
    for pattern in NEGATED_PROSE_CLAUSES:
        positive_text = re.sub(pattern, " ", positive_text, flags=re.IGNORECASE)
    action = matches_any(positive_text, PROSE_ACTIONS)
    scope = matches_any(positive_text, PROSE_SCOPE)
    punctuation_transform = matches_any(positive_text, PUNCTUATION_TRANSFORM)
    if matches_any(text, AUTHORSHIP_CLASSIFICATION):
        return False
    if matches_any(text, FACT_CHECK_ONLY) and not action:
        return False
    if matches_any(text, DOCS_QUESTION) and not action:
        return False
    if matches_any(text, CODE_ONLY) and not matches_any(positive_text, MIXED_PROSE_SCOPE):
        return False
    return action and (scope or punctuation_transform)


def route_prompt(prompt: str) -> list[str]:
    """Return references only when the prompt clearly asks for prose help."""

    text = prompt.strip().lower()
    if not text or not has_prose_request(text):
        return []
    references: list[str] = []
    for rule in ROUTE_RULES:
        if matches_any(text, rule.patterns):
            add_unique(references, (rule.reference,))
    if "references/operating-contract.md" not in references:
        references.insert(0, "references/operating-contract.md")
    if not references:
        references.extend(("references/operating-contract.md", "references/foundations.md"))
    return references


def run_case(case: TestCase) -> dict[str, object]:
    references = route_prompt(case.prompt)
    missing = [reference for reference in case.expect if reference not in references]
    forbidden = [reference for reference in case.forbid if reference in references]
    passed = not missing and not forbidden and (bool(case.expect) or not references)
    return {
        "name": case.name,
        "passed": passed,
        "expected": list(case.expect),
        "forbidden": list(case.forbid),
        "actual": references,
        "missing": missing,
        "unexpected": forbidden,
    }


def run_suite() -> dict[str, object]:
    checks = [run_case(case) for case in TEST_CASES]
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "summary": {"checks_total": len(checks), "checks_passed": sum(1 for check in checks if check["passed"])},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route clear prose requests to better-writing references.")
    parser.add_argument("--prompt", help="Prompt to route")
    parser.add_argument("--suite", action="store_true", help="Run the built-in routing suite")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format")
    args = parser.parse_args(argv)
    if args.suite:
        result: dict[str, object] = run_suite()
    elif args.prompt:
        result = {"prompt": args.prompt, "references": route_prompt(args.prompt)}
    else:
        parser.error("pass --prompt or --suite")
    if args.format == "json":
        print(json.dumps(result, indent=2))
    elif args.suite:
        summary = result["summary"]
        assert isinstance(summary, dict)
        print(f"Suite: {summary['checks_passed']}/{summary['checks_total']} passed")
        for check in result["checks"]:
            assert isinstance(check, dict)
            print(f"{'PASS' if check['passed'] else 'FAIL'}: {check['name']}")
    else:
        references = result["references"]
        assert isinstance(references, list)
        print("Recommended references:" if references else "No prose-writing route detected.")
        for reference in references:
            print(f"- {reference}")
    return 0 if not args.suite or bool(result["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
