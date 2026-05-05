#!/usr/bin/env bash
set -euo pipefail

AGENT="all"
PROJECT=""
OUTPUT_DIR=""
DRY_RUN=false
SINCE=""

# Auto-detect paths if not explicitly set
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -z "$OUTPUT_DIR" ]]; then
	# Try config module, fall back to default
	OUTPUT_DIR=$(python3 -c "from scripts.config import resolve_output_dir; print(resolve_output_dir())" 2>/dev/null || echo "./sessions/")
fi

usage() {
	cat <<'EOF'
Usage: ./extract.sh [OPTIONS]

Options:
  --agent {opencode|claude|gemini|all}   Which agent(s) to extract (default: all)
  --project {name}                       Filter to specific project
  --output-dir {path}                    Output directory (default: ./sessions/)
  --dry-run                              Show what would be extracted without writing
  --since {date}                         Only extract sessions since ISO 8601 date
  --all                                  Extract from all agents (default)
  -h, --help                             Show this help message
EOF
	exit 0
}

error() {
	echo "ERROR: $1" >&2
	exit 1
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--agent)
		[[ -z "${2:-}" ]] && error "--agent requires a value: {opencode|claude|gemini|all}"
		AGENT="$2"
		shift 2
		;;
	--project)
		[[ -z "${2:-}" ]] && error "--project requires a value"
		PROJECT="$2"
		shift 2
		;;
	--output-dir)
		[[ -z "${2:-}" ]] && error "--output-dir requires a path"
		OUTPUT_DIR="$2"
		shift 2
		;;
	--dry-run)
		DRY_RUN=true
		shift
		;;
	--since)
		[[ -z "${2:-}" ]] && error "--since requires a date"
		SINCE="$2"
		shift 2
		;;
	--all)
		AGENT="all"
		shift
		;;
	-h | --help)
		usage
		;;
	*)
		error "Unknown option: $1"
		;;
	esac
done

case "$AGENT" in
opencode | claude | gemini | all) ;;
*) error "Invalid agent '$AGENT'. Must be: opencode, claude, gemini, or all" ;;
esac

if [[ "$DRY_RUN" == false ]]; then
	mkdir -p "$OUTPUT_DIR" 2>/dev/null || error "Cannot create output directory: $OUTPUT_DIR"
	[[ -w "$OUTPUT_DIR" ]] || error "Output directory is not writable: $OUTPUT_DIR"
fi

COMMON_ARGS=("--output-dir" "$OUTPUT_DIR")
if [[ "$DRY_RUN" == true ]]; then
	COMMON_ARGS+=("--dry-run")
fi
if [[ -n "$PROJECT" ]]; then
	COMMON_ARGS+=("--project" "$PROJECT")
fi
if [[ -n "$SINCE" ]]; then
	COMMON_ARGS+=("--since" "$SINCE")
fi

if [[ "$AGENT" == "all" ]]; then
	AGENTS="opencode claude gemini"
else
	AGENTS="$AGENT"
fi

run_extractor() {
	local agent="$1"
	local script=""
	case "$agent" in
	opencode) script="$SCRIPTS_DIR/extract_opencode.py" ;;
	claude) script="$SCRIPTS_DIR/extract_claude_code.py" ;;
	gemini) script="$SCRIPTS_DIR/extract_gemini.py" ;;
	esac

	if [[ ! -f "$script" ]]; then
		echo "WARNING: $script not found, skipping $agent" >&2
		echo "SKIP"
		return
	fi

	echo ">>> Extracting $agent..."
	local output
	output=$(python3 "$script" "${COMMON_ARGS[@]}" 2>&1) || {
		echo "WARNING: $agent extractor failed" >&2
		echo "$output" >&2
		echo "ERR"
		return
	}
	echo "$output"

	local count
	count=$(echo "$output" | grep -oiE '[0-9]+ session[s]? (file)?(s)?(written|copied|would be)' | tail -1 | grep -oE '^[0-9]+' || echo "$output" | grep -oE 'DONE: [0-9]+' | tail -1 | grep -oE '[0-9]+' || true)
	echo "COUNT:$count"
}

RESULTS=""
TOTAL=0
HAS_NUMERIC=false

for a in $AGENTS; do
	result=$(run_extractor "$a")
	count_line=$(echo "$result" | grep "^COUNT:" | head -1)
	count_val="${count_line#COUNT:}"

	if [[ -z "$count_val" ]]; then
		count_label="skipped"
	elif [[ "$count_val" == "ERR" || "$count_val" == "SKIP" ]]; then
		count_label="$count_val"
	else
		count_label="$count_val"
		TOTAL=$((TOTAL + count_val))
		HAS_NUMERIC=true
	fi

	RESULTS="${RESULTS}${a}:${count_label}
"

	output_without_count=$(echo "$result" | grep -v "^COUNT:")
	echo "$output_without_count"
done

echo ""
echo "============================================"
echo "Extraction Complete"
echo "============================================"

for a in $AGENTS; do
	label="$(echo "${a:0:1}" | tr '[:lower:]' '[:upper:]')${a:1}:"
	count=$(echo "$RESULTS" | grep "^${a}:" | head -1 | cut -d: -f2)
	printf "%-10s %s sessions\n" "$label" "${count:-(none)}"
done

if [[ "$HAS_NUMERIC" == true ]]; then
	printf "%-10s %s sessions\n" "Total:" "$TOTAL"
fi
printf "%-10s %s\n" "Output:" "$OUTPUT_DIR"
