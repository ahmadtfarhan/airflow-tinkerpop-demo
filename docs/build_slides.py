#!/usr/bin/env python3
"""Build DemoSlides.pptx from SLIDES (below), with speaker notes and timings.

    pip install python-pptx && python docs/build_slides.py
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "DemoSlides.pptx"

INK = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x5A, 0x5A, 0x5A)
ACCENT = RGBColor(0x00, 0x7A, 0x87)   # Airflow teal
WARN = RGBColor(0xB0, 0x3A, 0x2B)

# (title, [bullets], speaker notes, timing label)
SLIDES: list[tuple[str, list[str], str, str]] = [
    (
        "Knowledge Graphs for AI, built with Airflow",
        [
            "Ahmad Farhan  ·  Data Engineer",
            "Author, apache-airflow-providers-apache-tinkerpop",
            "",
            "github.com/ahmadtfarhan/airflow-tinkerpop-demo",
        ],
        "Do NOT open with a bio. One line: 'I wrote the TinkerPop provider for "
        "Airflow. Today I want to show you what I built with it.' Then move.",
        "00:00",
    ),
    (
        "Your RAG can answer this",
        [
            "\"What tier is Northwind Analytics on?\"",
            "    -> Enterprise.  One document. Retrieval nails it.",
            "",
            "\"If Northwind Analytics is compromised, what is exposed?\"",
            "    -> ...",
        ],
        "Land the second question and PAUSE. Do not answer it yet. This is the "
        "question the whole talk exists to answer. No code on screen.",
        "00:30",
    ),
    (
        "The answer is in twenty-eight documents",
        [
            "No single ticket says it.",
            "",
            "Account linkage lives in call summaries.",
            "Entitlements live in access emails.",
            "Group inheritance lives in governance memos.",
            "",
            "The answer needs a JOIN, not a lookup.",
            "Top-k retrieval was never going to get there.",
        ],
        "This is the structural argument, and it is the core of the talk. "
        "Retrieval finds documents that LOOK like the question. Multi-hop "
        "answers do not look like the question -- they are assembled from "
        "pieces that individually look like nothing.",
        "01:30",
    ),
    (
        "Why I am the one telling you this",
        [
            "Previous role: user relationship graphs on Azure Cosmos DB.",
            "Needed recursive traversal for access inheritance.",
            "",
            "Custom gremlinpython hook  ->  a full Airflow provider.",
            "Chose Client over RemoteTraversalDriver for stability.",
            "",
            "pip install apache-airflow-providers-apache-tinkerpop",
        ],
        "TWO AND A HALF MINUTES, hard. This is credibility, not the talk. If "
        "you are running behind, this is the first thing to compress. Land on "
        "the pip install line and move on.",
        "03:00",
    ),
    (
        "One corpus. Two indexes. One orchestrator.",
        [
            "crm_corpus_generate  ->  [crm_corpus]",
            "                            |-> kg_build      (graph)",
            "                            \\-> vector_build  (vectors)",
            "",
            "215 support tickets. ~10k tokens.",
            "Same documents into both. Same model. Same output schema.",
        ],
        "Switch to the Airflow UI Assets view here and let the lineage do the "
        "explaining. Thirty seconds of pointing beats a minute of talking.",
        "05:00",
    ),
    (
        "An LLM read the tickets and built the graph",
        [
            "@task.llm(output_type=ExtractedTriples)  x22 batches",
            "        -> normalise to canonical ids  (plain Python, no LLM)",
            "        -> verify against gold edges   (fails below 0.95 F1)",
            "        -> GremlinOperator + bindings",
            "",
            "kg_build never opens the ground-truth CSV.",
        ],
        "Show the mapped extraction tasks in the grid, click one, show the "
        "ExtractedTriples XCom. Then say the last line OUT LOUD -- someone "
        "will ask, and answering before they ask is worth a lot.",
        "08:00",
    ),
    (
        "And the same tickets went into pgvector",
        [
            "DocumentLoaderOperator   <- identical config to kg_build",
            "gemini-embedding-001, 768 dimensions",
            "",
            "SELECT doc_id, text, 1 - (embedding <=> $1) AS score",
            "FROM crm_chunks ORDER BY embedding <=> $1 LIMIT $2;",
            "",
            "One chunk = one document. So top_k really means documents.",
        ],
        "Thirty seconds. The point of this slide is that the vector arm is "
        "real and configured fairly, not that vector search is interesting.",
        "09:00",
    ),
    (
        "How to not build a strawman",
        [
            "Same corpus, byte-identical. Same model. Same output schema.",
            "",
            "Retrieval budget FAVOURS vectors:",
            "    vector arm:  k = 5, 15, 25, 50  (up to a quarter of the corpus)",
            "    graph agent: 12 Gremlin queries, 13 model requests",
            "",
            "Third arm: the whole corpus in one prompt, no retrieval.",
            "Gold answers known by construction -> no LLM judge.",
        ],
        "Say this fast and confidently. This is the slide the skeptics in the "
        "room need, and hesitating over it reads as doubt. If anyone wants "
        "more, docs/METHODOLOGY.md is in the repo.",
        "09:30",
    ),
    (
        "Results",
        [
            "[ scorecard.png -- F1 by question type, three arms ]",
            "",
            "B1 lookup        vectors tie on quality, win on cost and latency",
            "B2 two-hop       graph, narrowly",
            "B3 transitive    graph, decisively",
            "B4 aggregation   graph, decisively",
            "B5 prose         VECTORS WIN OUTRIGHT. Graph F1 ~ 0.",
        ],
        "Insert the generated chart. Walk B1 -> B5 in order and be explicit "
        "about where vectors win. Finish on B3 so the live demo follows "
        "naturally. Quote macro-F1, and say the word 'macro'.",
        "11:00",
    ),
    (
        "Live",
        [
            "rag_showdown_live   question_id = B3-01",
            "",
            "\"Through its network of linked accounts,",
            "  which resources can Northwind Analytics ultimately reach?\"",
            "",
            "Nothing is extracted. Nothing is re-embedded.",
            "This only queries.",
        ],
        "THE ONLY LIVE MOMENT. ~40s of dead air -- narrate the four tasks as "
        "they go green. Then open the graph_answer logs and show the Gremlin "
        "THE AGENT WROTE ITSELF. That is the moment of the talk.\n\n"
        "If a task goes red: Clear Task. durable=True replays the cached "
        "steps -- and say so, it is a better story than the failure.\n"
        "Still red at 14:00: 'here is the one I ran this morning' and click "
        "the previous successful run in the same grid. Same screen, one click.",
        "12:30",
    ),
    (
        "Six hops, twenty-eight documents",
        [
            "g.V().hasLabel('Customer').has('id','C059')",
            " .union(__.identity(),",
            "   __.repeat(__.bothE('RELATED_TO')",
            "     .has('relation_type','CUSTOMER_LINK')",
            "     .otherV().simplePath()).emit().times(6))",
            " .dedup()",
            "",
            "Largest cluster: 19 accounts, diameter 7.",
            "28 of 215 documents carry the facts. Top-5 sees 5 of them.",
            "At k=50 it still cannot answer.",
        ],
        "The last line is the strongest honest result in the deck: retrieving "
        "ten times more documents does not help, because the answer was never "
        "in any single one of them.\n\n"
        "Verified on B4-05: at k=50 the vector arm replied 'the provided "
        "documents do not contain information about any Churned accounts that "
        "hold access to a Confidential resource'. It did not hallucinate -- it "
        "could not do the join.",
        "14:30",
    ),
    (
        "The honest slide",
        [
            "Graphs are not free:",
            "    22 LLM calls to BUILD it, every time the corpus changes.",
            "    Extraction edge-F1 was 1.00 here; I gate the DAG at 0.95,",
            "    because at 0.88 a six-hop closure is a coin flip.",
            "",
            "Vectors win on lookup (cost, latency) and on prose outright.",
            "Full context beats vectors on easy questions -- at 20-40x cost.",
            "",
            "The answer is HYBRID. Graph for structure, vectors for prose,",
            "Airflow for both.",
        ],
        "Do not skip this to save time. Being the person who showed where "
        "their own approach loses is what makes the rest of it believable.\n\n"
        "If asked why 0.95: every hop needs its edge extracted correctly, and "
        "the failures multiply. At 0.88 edge-F1, a six-hop chain survives only "
        "0.88^6 = 46% of the time -- the graph arm would lose B3 for reasons "
        "that have nothing to do with graphs.\n\n"
        "Note the demo-day run replays a committed extraction and makes ZERO "
        "calls. The ~22 is what it costs to build, not to show.",
        "16:00",
    ),
    (
        "What I learned about my own provider",
        [
            "Three you find by using it:",
            "  1.  GremlinOperator has no bindings= parameter at all.",
            "  2.  run() closes the client on EVERY call -- 100 vertices,",
            "        100 websocket handshakes.",
            "  3.  run(query, serializer, bindings, request_options) is not",
            "        agent-shaped: unannotated params become open schema.",
            "",
            "Three more once an LLM is driving it:",
            "  4.  The hook cannot be called from an async context AT ALL.",
            "  5.  A rejected traversal kills the task instead of reaching",
            "        the model, so every retry writes the same bad query.",
            "  6.  A usage limit is a hard task failure -- it took a whole",
            "        43-question sweep down with it.",
        ],
        "Best possible ending for a provider-author talk: you used your own "
        "tool hard enough to find its edges. Invite contributors.\n\n"
        "The split is the point. The first three are ordinary API gaps. The "
        "second three only exist because an LLM is the caller -- and 4 is the "
        "sharpest: the hook cannot be used in the very context HookToolset "
        "exists to create. That is not a missing feature, it is an "
        "incompatibility.\n\n"
        "If short on time, read the two headings and stop. The six are listed "
        "in the README.",
        "18:00",
    ),
    (
        "Thank you",
        [
            "github.com/ahmadtfarhan/airflow-tinkerpop-demo",
            "    docs/METHODOLOGY.md  -- the full evaluation protocol",
            "",
            "apache-airflow-providers-apache-tinkerpop",
            "apache-airflow-providers-common-ai",
            "",
            "Contributions welcome -- apache/airflow, tag @ahmadtfarhan",
            "",
            "Questions?",
        ],
        "Leave this up for Q&A. Have the scorecard and the agent tool-call "
        "screenshot ready to jump back to.",
        "20:00",
    ),
]


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    for title, bullets, notes, timing in SLIDES:
        slide = prs.slides.add_slide(blank)

        box = slide.shapes.add_textbox(Inches(0.8), Inches(0.6),
                                       Inches(11.7), Inches(1.1))
        para = box.text_frame.paragraphs[0]
        para.text = title
        para.font.size = Pt(40)
        para.font.bold = True
        para.font.color.rgb = INK

        body = slide.shapes.add_textbox(Inches(0.9), Inches(2.0),
                                        Inches(11.5), Inches(4.6))
        frame = body.text_frame
        frame.word_wrap = True
        for i, line in enumerate(bullets):
            p = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
            p.text = line
            mono = line.startswith(("g.", " ", "@", "SELECT", "FROM", "crm_", "pip", "rag_"))
            p.font.size = Pt(20 if mono else 24)
            p.font.name = "Menlo" if mono else "Helvetica Neue"
            p.font.color.rgb = MUTED if line.startswith("    ") else INK
            if line.isupper() and line.strip():
                p.font.color.rgb = WARN
            p.space_after = Pt(8)

        stamp = slide.shapes.add_textbox(Inches(11.9), Inches(6.85),
                                         Inches(1.2), Inches(0.4))
        sp = stamp.text_frame.paragraphs[0]
        sp.text = timing
        sp.font.size = Pt(13)
        sp.font.color.rgb = ACCENT
        sp.font.bold = True

        slide.notes_slide.notes_text_frame.text = f"[{timing}]  {notes}"

    prs.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path} ({len(SLIDES)} slides)")
