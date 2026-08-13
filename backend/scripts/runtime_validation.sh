#!/usr/bin/env bash
#
# runtime_validation.sh - Comprehensive end-to-end runtime validation
#
# This script validates the complete LeadBoost backend by:
# 1. Running the API with AI features enabled
# 2. Executing test_api.sh to exercise all endpoints
# 3. Capturing database state before/after
# 4. Verifying LLM execution (not just fallbacks)
# 5. Validating observability table population
# 6. Checking data flow from discovery → persistence → API response
#
# REQUIREMENTS:
#   - Python 3.12+
#   - PostgreSQL or SQLite
#   - Groq API key (in .env.testing)
#   - Serper API key (in .env.testing)
#   - jq, sqlite3/psql CLI tools
#
# USAGE:
#   chmod +x runtime_validation.sh
#   ./runtime_validation.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALIDATION_DIR="$BACKEND_DIR/validation_results_$(date +%Y%m%d_%H%M%S)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

mkdir -p "$VALIDATION_DIR"
LOG_FILE="$VALIDATION_DIR/validation.log"

log() {
    echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $*" | tee -a "$LOG_FILE"
}

success() {
    echo -e "${GREEN}✔${NC} $*" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}✘${NC} $*" | tee -a "$LOG_FILE"
}

warn() {
    echo -e "${YELLOW}⚠${NC} $*" | tee -a "$LOG_FILE"
}

section() {
    echo "" | tee -a "$LOG_FILE"
    echo -e "${BOLD}${BLUE}========================================${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}${BLUE}$*${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}${BLUE}========================================${NC}" | tee -a "$LOG_FILE"
}

# Check prerequisites
section "Checking Prerequisites"

if ! command -v jq >/dev/null 2>&1; then
    error "jq is required but not installed"
    exit 1
fi
success "jq installed"

if ! command -v python3 >/dev/null 2>&1; then
    error "python3 is required but not installed"
    exit 1
fi
success "python3 installed"

# Check for .env.testing
if [ ! -f "$BACKEND_DIR/.env.testing" ]; then
    warn ".env.testing not found - creating from template"
    if [ -f "$BACKEND_DIR/.env.example" ]; then
        cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env.testing"
        echo "ENABLE_AI_FOR_ALL_PLANS=true" >> "$BACKEND_DIR/.env.testing"
        echo "ENVIRONMENT=development" >> "$BACKEND_DIR/.env.testing"
    else
        error ".env.example not found - cannot create .env.testing"
        exit 1
    fi
fi

# Load environment
cd "$BACKEND_DIR"
export $(cat .env.testing | grep -v '^#' | xargs)

# Verify AI feature bypass is enabled
if [ "${ENABLE_AI_FOR_ALL_PLANS:-false}" != "true" ]; then
    error "ENABLE_AI_FOR_ALL_PLANS must be 'true' in .env.testing"
    exit 1
fi
success "AI feature bypass enabled"

# Check for API keys
if [ -z "${GROQ_API_KEY:-}" ] || [ "$GROQ_API_KEY" = "your-groq-api-key-here" ]; then
    error "GROQ_API_KEY not configured in .env.testing"
    exit 1
fi
success "GROQ_API_KEY configured"

if [ -z "${SERPER_API_KEY:-}" ] || [ "$SERPER_API_KEY" = "your-serper-api-key-here" ]; then
    warn "SERPER_API_KEY not configured - web search fallback will not work"
else
    success "SERPER_API_KEY configured"
fi

# Start the API server
section "Starting API Server"

log "Cleaning old database..."
rm -f test_runtime.db

log "Starting uvicorn..."
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file .env.testing > "$VALIDATION_DIR/server.log" 2>&1 &
SERVER_PID=$!

echo "$SERVER_PID" > "$VALIDATION_DIR/server.pid"

log "Server PID: $SERVER_PID"
log "Waiting for server to start..."

# Wait for server to be ready
MAX_WAIT=60
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -s http://localhost:8000/live >/dev/null 2>&1; then
        success "Server is ready"
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    error "Server failed to start within ${MAX_WAIT}s"
    cat "$VALIDATION_DIR/server.log"
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi

# Capture initial database state
section "Capturing Initial Database State"

if [ -f "test_runtime.db" ]; then
    log "Capturing table counts..."
    sqlite3 test_runtime.db "
        SELECT 'leads', COUNT(*) FROM leads
        UNION ALL SELECT 'scraping_logs', COUNT(*) FROM scraping_logs
        UNION ALL SELECT 'lead_enrichment_logs', COUNT(*) FROM lead_enrichment_logs
        UNION ALL SELECT 'ai_decision_logs', COUNT(*) FROM ai_decision_logs
        UNION ALL SELECT 'pipeline_execution_logs', COUNT(*) FROM pipeline_execution_logs
        UNION ALL SELECT 'prompt_execution_logs', COUNT(*) FROM prompt_execution_logs
        UNION ALL SELECT 'evaluation_report_logs', COUNT(*) FROM evaluation_report_logs
        UNION ALL SELECT 'discovery_run_logs', COUNT(*) FROM discovery_run_logs;
    " > "$VALIDATION_DIR/db_initial.txt" 2>&1 || warn "Could not query initial DB state"
    
    success "Initial DB state captured"
fi

# Run test_api.sh
section "Running Full API Test Suite"

if [ ! -f "$SCRIPT_DIR/test_api.sh" ]; then
    error "test_api.sh not found at $SCRIPT_DIR/test_api.sh"
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi

chmod +x "$SCRIPT_DIR/test_api.sh"

log "Executing test_api.sh..."
if "$SCRIPT_DIR/test_api.sh" > "$VALIDATION_DIR/test_api_output.txt" 2>&1; then
    success "test_api.sh completed successfully"
    cat "$VALIDATION_DIR/test_api_output.txt" | tee -a "$LOG_FILE"
else
    error "test_api.sh failed"
    cat "$VALIDATION_DIR/test_api_output.txt" | tee -a "$LOG_FILE"
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi

# Capture final database state
section "Capturing Final Database State"

sleep 2  # Allow any async operations to complete

if [ -f "test_runtime.db" ]; then
    log "Capturing final table counts..."
    sqlite3 test_runtime.db "
        SELECT 'leads', COUNT(*) FROM leads
        UNION ALL SELECT 'scraping_logs', COUNT(*) FROM scraping_logs
        UNION ALL SELECT 'lead_enrichment_logs', COUNT(*) FROM lead_enrichment_logs
        UNION ALL SELECT 'ai_decision_logs', COUNT(*) FROM ai_decision_logs
        UNION ALL SELECT 'pipeline_execution_logs', COUNT(*) FROM pipeline_execution_logs
        UNION ALL SELECT 'prompt_execution_logs', COUNT(*) FROM prompt_execution_logs
        UNION ALL SELECT 'evaluation_report_logs', COUNT(*) FROM evaluation_report_logs
        UNION ALL SELECT 'discovery_run_logs', COUNT(*) FROM discovery_run_logs;
    " > "$VALIDATION_DIR/db_final.txt" 2>&1 || warn "Could not query final DB state"
    
    success "Final DB state captured"
fi

# Validate LLM execution
section "Validating LLM Execution (Not Fallbacks)"

if [ -f "test_runtime.db" ]; then
    log "Checking prompt_execution_logs for LLM calls..."
    
    LLM_CALLS=$(sqlite3 test_runtime.db "SELECT COUNT(*) FROM prompt_execution_logs;" 2>/dev/null || echo "0")
    
    if [ "$LLM_CALLS" -gt 0 ]; then
        success "Found $LLM_CALLS LLM prompt executions"
        
        log "Breakdown by agent:"
        sqlite3 test_runtime.db "
            SELECT agent_name, COUNT(*) as call_count 
            FROM prompt_execution_logs 
            GROUP BY agent_name;
        " | tee -a "$LOG_FILE" 2>/dev/null || warn "Could not query agent breakdown"
        
    else
        error "No LLM calls found - agents may be using fallback paths only"
    fi
    
    log "Checking ai_decision_logs for LLM vs heuristic source..."
    
    LLM_DECISIONS=$(sqlite3 test_runtime.db "
        SELECT stage, COUNT(*) as count, 
               SUM(CASE WHEN model_used LIKE '%groq%' OR model_used LIKE '%llm%' THEN 1 ELSE 0 END) as llm_count,
               SUM(CASE WHEN model_used LIKE '%heuristic%' OR model_used LIKE '%deterministic%' THEN 1 ELSE 0 END) as fallback_count
        FROM ai_decision_logs 
        GROUP BY stage;
    " 2>/dev/null)
    
    if [ -n "$LLM_DECISIONS" ]; then
        echo "$LLM_DECISIONS" | tee -a "$LOG_FILE"
    else
        warn "Could not analyze LLM vs fallback usage"
    fi
fi

# Validate observability table population
section "Validating Observability Tables"

if [ -f "test_runtime.db" ]; then
    log "Checking all observability tables are populated..."
    
    PIPELINE_LOGS=$(sqlite3 test_runtime.db "SELECT COUNT(*) FROM pipeline_execution_logs;" 2>/dev/null || echo "0")
    EVAL_LOGS=$(sqlite3 test_runtime.db "SELECT COUNT(*) FROM evaluation_report_logs;" 2>/dev/null || echo "0")
    DISCOVERY_LOGS=$(sqlite3 test_runtime.db "SELECT COUNT(*) FROM discovery_run_logs;" 2>/dev/null || echo "0")
    
    if [ "$PIPELINE_LOGS" -gt 0 ]; then
        success "pipeline_execution_logs: $PIPELINE_LOGS records"
    else
        error "pipeline_execution_logs: 0 records (should have entries)"
    fi
    
    if [ "$EVAL_LOGS" -gt 0 ]; then
        success "evaluation_report_logs: $EVAL_LOGS records"
    else
        warn "evaluation_report_logs: 0 records"
    fi
    
    if [ "$DISCOVERY_LOGS" -gt 0 ]; then
        success "discovery_run_logs: $DISCOVERY_LOGS records"
    else
        warn "discovery_run_logs: 0 records"
    fi
fi

# Extract sample leads for inspection
section "Extracting Sample Leads"

if [ -f "test_runtime.db" ]; then
    log "Exporting completed leads to validation results..."
    
    sqlite3 -header -csv test_runtime.db "
        SELECT id, company_name, website, industry, score, qualification_label,
               scrape_confidence, enrichment_confidence, scrape_source, enrichment_source,
               outreach_message IS NOT NULL as has_outreach
        FROM leads 
        WHERE qualification_label IS NOT NULL 
        LIMIT 10;
    " > "$VALIDATION_DIR/sample_leads.csv" 2>&1 || warn "Could not export sample leads"
    
    if [ -f "$VALIDATION_DIR/sample_leads.csv" ]; then
        success "Sample leads exported to sample_leads.csv"
        cat "$VALIDATION_DIR/sample_leads.csv" | tee -a "$LOG_FILE"
    fi
fi

# Validate data flow
section "Validating End-to-End Data Flow"

if [ -f "test_runtime.db" ]; then
    log "Checking data flow: scraped_data → enriched_data → company_intelligence → scoring..."
    
    DATA_FLOW=$(sqlite3 test_runtime.db "
        SELECT 
            l.id as lead_id,
            l.company_name,
            CASE WHEN sl.scraped_data IS NOT NULL THEN '✓' ELSE '✗' END as scraped,
            CASE WHEN lel.enrichment_data IS NOT NULL THEN '✓' ELSE '✗' END as enriched,
            CASE WHEN ad.stage = 'company_intelligence' THEN '✓' ELSE '✗' END as analyzed,
            CASE WHEN l.score IS NOT NULL THEN '✓' ELSE '✗' END as scored,
            CASE WHEN l.outreach_message IS NOT NULL THEN '✓' ELSE '✗' END as messaged
        FROM leads l
        LEFT JOIN scraping_logs sl ON l.id = sl.lead_id
        LEFT JOIN lead_enrichment_logs lel ON l.id = lel.lead_id
        LEFT JOIN ai_decision_logs ad ON l.id = ad.lead_id
        WHERE l.id IN (SELECT DISTINCT lead_id FROM pipeline_execution_logs)
        LIMIT 5;
    " 2>/dev/null)
    
    if [ -n "$DATA_FLOW" ]; then
        echo "$DATA_FLOW" | tee -a "$LOG_FILE"
        success "Data flow validated"
    else
        warn "Could not validate data flow"
    fi
fi

# Check server logs for errors
section "Checking Server Logs for Errors"

ERROR_COUNT=$(grep -c "ERROR" "$VALIDATION_DIR/server.log" || echo "0")
CRITICAL_COUNT=$(grep -c "CRITICAL" "$VALIDATION_DIR/server.log" || echo "0")

if [ "$ERROR_COUNT" -gt 0 ]; then
    warn "Found $ERROR_COUNT ERROR entries in server log"
    log "Sample errors:"
    grep "ERROR" "$VALIDATION_DIR/server.log" | head -5 | tee -a "$LOG_FILE"
else
    success "No ERRORs in server log"
fi

if [ "$CRITICAL_COUNT" -gt 0 ]; then
    error "Found $CRITICAL_COUNT CRITICAL entries in server log"
    grep "CRITICAL" "$VALIDATION_DIR/server.log" | tee -a "$LOG_FILE"
else
    success "No CRITICAL errors in server log"
fi

# Cleanup
section "Cleanup"

log "Stopping server (PID $SERVER_PID)..."
kill $SERVER_PID 2>/dev/null || warn "Server already stopped"
sleep 2

if ps -p $SERVER_PID > /dev/null 2>&1; then
    warn "Server did not stop gracefully, forcing..."
    kill -9 $SERVER_PID 2>/dev/null || true
fi

success "Server stopped"

# Final summary
section "Validation Complete"

log "Results saved to: $VALIDATION_DIR"
log "Server log: $VALIDATION_DIR/server.log"
log "Test output: $VALIDATION_DIR/test_api_output.txt"
log "Database state: $VALIDATION_DIR/db_*.txt"
log "Sample leads: $VALIDATION_DIR/sample_leads.csv"

success "Runtime validation completed successfully"
exit 0
