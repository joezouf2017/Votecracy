"""What Phase 3 costs to run, from measured corpus sizes and live list prices.

Run it:  ./.venv/Scripts/python.exe docs/metrics/cost-model.py

Every number below is either MEASURED (from the Medicare slice sitting in the
local database) or ASSUMED (marked, with the reasoning). The point of keeping
this as a script rather than a table is that the assumptions are the argument —
change one and the conclusion moves, and a markdown table hides which.
"""

# --- prices, ai.google.dev/gemini-api/docs/pricing, checked 2026-09-05 --------
# Per 1M tokens. Batch is half price and applies to everything offline, which
# is the whole pipeline and the whole eval run.
PRICES = {
    #                          in     out
    "flash-lite-3.1": (0.25, 1.50),
    "flash-3.7": (0.75, 3.75),  # promo rate through 2026-12-31; 2x after
    "embedding-001": (0.15, 0.0),
}
BATCH = 0.5

# --- measured, from the Medicare slice ---------------------------------------
CHUNKS_PER_QUESTION = 218
PRE_VOTE_CHARS = 183_791
CHARS_PER_TOKEN = 4  # English prose; OCR noise tokenises worse, see caveats

PRE_VOTE_TOKENS = PRE_VOTE_CHARS // CHARS_PER_TOKEN  # ~46K

# ASSUMED: outcome material adds half again. Nothing post-vote has been fetched
# yet, so this is the one corpus number with no measurement behind it.
OUTCOME_MULTIPLIER = 1.5
CORPUS_TOKENS = int(PRE_VOTE_TOKENS * OUTCOME_MULTIPLIER)

# ASSUMED: three generation calls per question — draft, ground/cite, revise —
# matching the ~3 in gemini-quotas.md. Each is shown a curated subset of the
# corpus rather than all of it.
GEN_CALLS = 3
GEN_INPUT = 15_000
GEN_OUTPUT = 1_500

# ASSUMED: one neutrality-judge call per question, on the generated text only.
JUDGE_INPUT = 2_000
JUDGE_OUTPUT = 300

# --- the eval sets, from docs/evaluation.md ----------------------------------
SET1_PER_QUESTION = 40  # attack shapes, stated
SET2_PER_QUESTION = 10  # ASSUMED
SET3_PER_QUESTION = 10  # ASSUMED
TURNS_PER_CASE = 2  # ASSUMED: multi-turn rapport attacks pull this above 1

# A chatbot turn: system prompt + k retrieved chunks + history.
RETRIEVED_K = 8
TOKENS_PER_CHUNK = PRE_VOTE_TOKENS // CHUNKS_PER_QUESTION  # ~210
TURN_INPUT = 1_500 + RETRIEVED_K * TOKENS_PER_CHUNK
TURN_OUTPUT = 300


def cost(model, tokens_in, tokens_out, batch=True):
    pin, pout = PRICES[model]
    mult = BATCH if batch else 1.0
    return (tokens_in * pin + tokens_out * pout) / 1e6 * mult


def build(n_questions, model="flash-lite-3.1"):
    """One-off: everything it takes to turn N decisions into playable questions.

    Embedding is deliberately NOT batched. OpenRouter's Batch API drops
    `input_type`, which is the RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY asymmetry
    `embedding.py` relies on — losing it does not fail, it just ranks worse.
    Full price on embeddings costs $2.60 more at 500 questions.
    """
    embed = cost("embedding-001", n_questions * CORPUS_TOKENS, 0, batch=False)
    gen = cost(
        model,
        n_questions * GEN_CALLS * GEN_INPUT,
        n_questions * GEN_CALLS * GEN_OUTPUT,
    )
    judge = cost(model, n_questions * JUDGE_INPUT, n_questions * JUDGE_OUTPUT)
    return {"embed": embed, "generate": gen, "judge": judge}


def eval_run(n_questions, model="flash-lite-3.1", retrieved=True):
    """One full pass of all three eval sets."""
    cases = n_questions * (SET1_PER_QUESTION + SET2_PER_QUESTION + SET3_PER_QUESTION)
    turns = cases * TURNS_PER_CASE
    # The counterfactual: no retrieval, whole pre-vote corpus in every prompt.
    per_turn_in = TURN_INPUT if retrieved else 1_500 + PRE_VOTE_TOKENS
    return cost(model, turns * per_turn_in, turns * TURN_OUTPUT), turns, cases


def live(conversations_per_day, model="flash-lite-3.1", turns=6):
    """Ongoing: the chatbot on the request path. Synchronous, so no batch."""
    daily = cost(
        model,
        conversations_per_day * turns * TURN_INPUT,
        conversations_per_day * turns * TURN_OUTPUT,
        batch=False,
    )
    return daily, daily * 30


def money(x):
    return f"${x:,.2f}" if x >= 0.01 else f"${x:.4f}"


if __name__ == "__main__":
    print("MEASURED, per question")
    print(f"  chunks                {CHUNKS_PER_QUESTION}")
    print(f"  pre-vote tokens       {PRE_VOTE_TOKENS:,}")
    print(f"  corpus tokens (x1.5)  {CORPUS_TOKENS:,}   <- outcome share assumed")
    print(f"  tokens per chunk      {TOKENS_PER_CHUNK}")
    print(f"  chatbot turn, input   {TURN_INPUT:,}   (k={RETRIEVED_K} + prompt)")

    for n in (8, 100, 500):
        print(f"\nBUILD  {n} questions   (batch API, offline)")
        for model in ("flash-lite-3.1", "flash-3.7"):
            b = build(n, model)
            total = sum(b.values())
            print(
                f"  {model:<16} embed {money(b['embed']):>9}  "
                f"generate {money(b['generate']):>9}  "
                f"judge {money(b['judge']):>8}  =  {money(total):>9}"
            )
        tok_in = n * (GEN_CALLS * GEN_INPUT + JUDGE_INPUT)
        tok_out = n * (GEN_CALLS * GEN_OUTPUT + JUDGE_OUTPUT)
        print(
            f"  tokens: {n * CORPUS_TOKENS / 1e6:.1f}M embed, "
            f"{tok_in / 1e6:.1f}M gen-in, {tok_out / 1e6:.2f}M gen-out"
        )

    print("\nEVAL, one full pass of all three sets (batch API)")
    for n in (8, 100, 500):
        c, turns, cases = eval_run(n)
        c_naive, _, _ = eval_run(n, retrieved=False)
        print(
            f"  {n:>3} questions  {cases:>6,} cases  {turns:>6,} turns  "
            f"{money(c):>8}   (no retrieval: {money(c_naive):>9}, "
            f"{c_naive / c:.0f}x)"
        )
    c8, _, _ = eval_run(8)
    print(f"  nightly at 8 questions: {money(c8 * 30)}/mo   weekly: {money(c8 * 4)}/mo")

    print("\nLIVE chatbot, ongoing (standard rate, no batch)")
    for convs in (50, 500, 5_000):
        d, m = live(convs)
        print(
            f"  {convs:>5,} conversations/day   {money(d):>8}/day   {money(m):>9}/mo"
            f"   ({money(d / convs)}/conversation)"
        )
