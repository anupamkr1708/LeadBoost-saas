"""
Business Discovery Layer.

Sits *before* the existing LeadPipeline, translating a natural-language
business search ("Top shoe stores in Mumbai") into validated Lead rows
that are then handed, unchanged, to LeadPipeline.execute() -- the same
entry point already used by the manual lead-creation endpoints.

Deterministic pipeline, no LLM anywhere in this package:

    natural language query
        -> query_parser.QueryParser            (regex, category+location+limit)
        -> providers.overpass_provider         (primary business search)
        -> business_normalizer                  (raw tags -> BusinessCandidate)
        -> website_resolver                      (Overpass website, else
                                                   providers.brave_provider
                                                   fallback for resolution only)
        -> website_validator                     (reachability + directory
                                                   -domain rejection)
        -> duplicate_detector                    (domain/name+phone dedup)
        -> ranking                               (deterministic scoring)
        -> discovery_service.DiscoveryService    (Lead creation + hands off
                                                   to the existing
                                                   LeadPipeline.execute())

Every component here is intentionally small and single-purpose -- see each
module's docstring. Nothing in this package modifies Core, the LangGraph
workflow, or any AI agent.
"""
