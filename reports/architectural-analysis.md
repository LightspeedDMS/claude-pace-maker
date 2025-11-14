# Claude Pace Maker - Architectural Analysis Report

## Executive Summary

The Claude Pace Maker epic aims to solve the critical problem of Claude Code burning through 5-hour credit limits during long missions, forcing developers to either stop work or incur overage charges. This architectural analysis evaluates the technical feasibility and provides recommendations for implementing an intelligent pacing system using Claude Code's hook infrastructure.

**Key Finding**: The epic is **technically feasible** using Claude Code's native hooks system with bash scripting, though there are significant architectural considerations around state management, API integration, and hook execution overhead.

---

## 1. Codebase Integration Analysis

### 1.1 Existing Architecture Assets

**Claude Usage Reporting Codebase** (`/home/jsbattig/Dev/claude-usage-reporting/`)
- **OAuth API Integration**: Fully implemented OAuth client for Claude API (`api.py`, `auth.py`)
- **Credential Management**: Auto-loads from `~/.claude/.credentials.json`
- **Usage Tracking**: Real-time monitoring with 30-second polling interval
- **State Persistence**: SQLite database for historical tracking (`~/.claude-usage/usage_history.db`)
- **Display Framework**: Rich terminal UI with progress bars and projections

**Claude Code Infrastructure**
- **Version**: 2.0.37 (confirmed installed)
- **Hooks System**: Native support for `PostToolUse` and `Stop` events
- **Transcript Format**: JSONL at `~/.claude/history.jsonl`
- **Settings Files**: Hierarchical configuration (user/project/local)

### 1.2 Integration Points

1. **Hook Events**
   - `PostToolUse`: Perfect for adaptive throttling after tool executions
   - `Stop`: Ideal for momentum preservation and completion validation
   - `SessionStart`: Can initialize state and load configuration

2. **API Access Patterns**
   - OAuth endpoints: `https://api.anthropic.com/api/oauth/{usage,profile}`
   - Headers: Already implemented in `claude-usage-reporting`
   - Rate limit data: 5-hour window with utilization percentage

3. **State Management**
   - SQLite database pattern established
   - JSON state files for lightweight data
   - File locking considerations for concurrent access

### 1.3 Dependencies

**Required** (Already available):
- bash, jq, curl, bc (standard Linux tools)
- Python 3.6+ (for dashboard and complex logic)
- SQLite (for state persistence)

**Optional** (For enhanced features):
- flock (file locking)
- timeout (command timeouts)
- watch (dashboard updates)

### 1.4 Ecosystem Fit

The epic fits naturally within Claude Code's architecture:
- Hooks are the official extension mechanism
- TDD-Guard precedent shows complex hook implementations work
- OAuth API integration already proven viable
- No modifications to Claude Code core required

---

## 2. Technical Feasibility Assessment

### 2.1 Architectural Challenges

#### Challenge 1: Hook Execution Overhead
**Issue**: Each PostToolUse hook spawns a new shell process
**Impact**: 100ms-500ms latency per tool call
**Mitigation**:
- Minimize hook logic complexity
- Cache API responses (5-minute TTL)
- Use lightweight state checks before API calls

#### Challenge 2: API Rate Limits
**Issue**: Claude API has undocumented rate limits
**Impact**: Could throttle monitoring capability
**Mitigation**:
- Implement exponential backoff
- Cache responses aggressively
- Fall back to local state when API unavailable

#### Challenge 3: State Corruption Risk
**Issue**: Multiple hooks may execute simultaneously
**Impact**: Race conditions on state files
**Mitigation**:
- Use flock for file locking
- Atomic write operations (write-rename pattern)
- SQLite for ACID guarantees on critical state

#### Challenge 4: Infinite Loop Prevention
**Issue**: Stop hook could repeatedly block completion
**Impact**: Claude session becomes unresponsive
**Mitigation**:
- Maximum retry counter in state
- Time-based circuit breaker
- Emergency kill switch via environment variable

### 2.2 Technical Complexity Analysis

**Low Complexity**:
- Hook installation and configuration
- Basic throttling delays (sleep commands)
- Environment variable parsing

**Medium Complexity**:
- API integration with caching
- State persistence with locking
- Logarithmic curve calculations

**High Complexity**:
- Transcript parsing for acceptance criteria
- Momentum preservation logic
- Dashboard with live updates

### 2.3 Resource Implications

**CPU**: Minimal (<1% average)
- Hook processes are short-lived
- API calls are infrequent (cached)

**Memory**: Negligible (<10MB)
- Small state files
- No persistent processes

**Disk I/O**: Low
- State updates every 30-60 seconds
- SQLite WAL mode for efficiency

**Network**: Minimal
- API calls every 5 minutes (cached)
- ~1KB per request

### 2.4 Critical Constraints

1. **60-second hook timeout**: Must complete quickly or risk termination
2. **No hook modification mid-session**: Configuration changes require restart
3. **JSON parsing in bash**: Complex but manageable with jq
4. **No direct Claude control**: Can only delay/block, not modify behavior

---

## 3. Architecture Research & Recommendations

### 3.1 Architectural Patterns Evaluation

#### Option 1: Pure Hooks Architecture (RECOMMENDED)
**Pattern**: Stateless hooks with shared state file

**Pros**:
- Native Claude Code integration
- No background processes
- Simple deployment
- Automatic lifecycle management

**Cons**:
- Bash scripting limitations
- Per-execution overhead
- Complex state management

**Implementation**:
```bash
# PostToolUse hook
#!/bin/bash
STATE_FILE="$HOME/.claude-pace-maker/state.json"
DELAY=$(calculate_adaptive_delay "$STATE_FILE")
sleep "$DELAY"
```

#### Option 2: Daemon Process Architecture
**Pattern**: Background service with hook communication

**Pros**:
- Persistent state in memory
- Complex logic in Python
- Real-time monitoring

**Cons**:
- Process management complexity
- Not native to Claude Code
- Requires systemd/init setup

#### Option 3: MCP Server Architecture
**Pattern**: Model Context Protocol server

**Pros**:
- Official protocol for extensions
- Rich interaction capabilities
- Future-proof

**Cons**:
- Not yet available for Claude Code
- Requires significant development
- Uncertain timeline

### 3.2 Technology Stack Recommendations

**Primary Implementation** (Bash + JSON):
```
PostToolUse Hook: pace-maker-throttle.sh
Stop Hook: pace-maker-momentum.sh
State Management: JSON files with flock
API Client: curl with jq parsing
Calculations: bc for logarithmic curves
```

**Dashboard Implementation** (Python):
```
CLI Tool: pace-maker-status
Display: Rich library (like claude-usage)
State Reader: JSON/SQLite integration
Updates: watch or internal loop
```

### 3.3 State Management Architecture

**Recommended Structure**:
```json
{
  "session": {
    "id": "uuid",
    "start_time": "ISO8601",
    "epic_spec": "path/to/spec.md"
  },
  "usage": {
    "last_check": "ISO8601",
    "utilization": 85.5,
    "credits_used": 42000,
    "reset_at": "ISO8601"
  },
  "pacing": {
    "current_delay": 15,
    "tool_count": 127,
    "last_tool_time": "ISO8601",
    "momentum_blocks": 0
  },
  "cache": {
    "api_response": {},
    "expires_at": "ISO8601"
  }
}
```

### 3.4 Scalability Considerations

**Multi-Session Support**:
- Separate state files by session ID
- Cleanup old sessions after 24 hours
- Global settings with per-session overrides

**Performance Optimization**:
- Lazy API calls (only when needed)
- Progressive delay increases
- Circuit breaker for API failures

**Future Extensions**:
- Machine learning for usage prediction
- Team-wide usage coordination
- Cost allocation and reporting

---

## 4. Implementation Architecture

### 4.1 Component Architecture

```
┌─────────────────────────────────────────────────┐
│                 Claude Code                      │
│  ┌──────────────────────────────────────────┐   │
│  │            Hooks System                  │   │
│  │  ┌────────────┐      ┌──────────────┐  │   │
│  │  │PostToolUse │      │     Stop      │  │   │
│  │  └──────┬─────┘      └───────┬──────┘  │   │
│  └─────────┼────────────────────┼──────────┘   │
└────────────┼────────────────────┼───────────────┘
             │                    │
             ▼                    ▼
    ┌────────────────┐    ┌──────────────┐
    │ Throttle Hook  │    │ Momentum Hook │
    └────────┬───────┘    └──────┬───────┘
             │                    │
             ▼                    ▼
    ┌────────────────────────────────────┐
    │         Shared State File          │
    │      ~/.claude-pace-maker/         │
    │         state.json                 │
    └────────────────────────────────────┘
             │
             ▼
    ┌────────────────────────────────────┐
    │        Claude API Client           │
    │    (OAuth + Usage Endpoints)       │
    └────────────────────────────────────┘
```

### 4.2 Hook Implementation Details

**PostToolUse Hook** (`pace-maker-throttle.sh`):
1. Read hook input JSON from stdin
2. Load current state file (with lock)
3. Check API cache validity
4. If expired, fetch fresh usage data
5. Calculate adaptive delay (logarithmic)
6. Update state with new metrics
7. Sleep for calculated delay
8. Exit 0 (success)

**Stop Hook** (`pace-maker-momentum.sh`):
1. Read hook input JSON from stdin
2. Parse transcript for completion status
3. Load epic spec if configured
4. Check acceptance criteria completion
5. If incomplete and under threshold:
   - Exit 2 with "Continue working" message
   - Increment block counter in state
6. Else allow stop (exit 0)

### 4.3 Installation Architecture

**Install Script** (`install.sh`):
```bash
#!/bin/bash
# 1. Check prerequisites
# 2. Create directory structure
# 3. Copy hook scripts
# 4. Generate settings.json
# 5. Initialize state file
# 6. Validate Claude credentials
# 7. Test API connectivity
```

**Directory Structure**:
```
~/.claude-pace-maker/
├── hooks/
│   ├── pace-maker-throttle.sh
│   └── pace-maker-momentum.sh
├── state/
│   ├── current.json
│   └── history.db
├── config/
│   └── settings.json
└── logs/
    └── pace-maker.log
```

---

## 5. Risk Analysis & Mitigation

### 5.1 Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| API changes break integration | Medium | High | Version check, graceful degradation |
| Hook timeout kills pacing | Low | Medium | Optimize for <5s execution |
| State corruption loses progress | Low | High | Atomic writes, backup state |
| Infinite blocking loops | Low | Critical | Circuit breaker, max retries |
| Performance degradation | Medium | Medium | Caching, async where possible |

### 5.2 Operational Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| User confusion about delays | High | Low | Clear dashboard, notifications |
| Overly aggressive throttling | Medium | Medium | Tunable parameters, overrides |
| Incomplete epic detection | Medium | Medium | Conservative defaults, manual override |

---

## 6. Final Recommendations

### 6.1 Architecture Decision

**RECOMMENDATION**: Implement using **Pure Hooks Architecture** with bash scripting.

**Rationale**:
1. Native Claude Code integration without external dependencies
2. Proven pattern (TDD-Guard precedent)
3. Simplest deployment and maintenance
4. No background process management
5. Automatic lifecycle with Claude sessions

### 6.2 Implementation Approach

**Phase 1**: Core Throttling (Week 1)
- PostToolUse hook with static delays
- Basic state management
- Manual testing

**Phase 2**: API Integration (Week 2)
- OAuth client integration
- Dynamic delay calculation
- Cache management

**Phase 3**: Momentum Preservation (Week 3)
- Stop hook implementation
- Transcript parsing
- Epic spec validation

**Phase 4**: Dashboard & Polish (Week 4)
- Python CLI dashboard
- User controls
- Documentation

### 6.3 Critical Success Factors

1. **Performance**: Hook execution must be <5 seconds
2. **Reliability**: Graceful degradation when API unavailable
3. **Usability**: Clear feedback about pacing status
4. **Safety**: Multiple kill switches for emergencies
5. **Compatibility**: Must work with existing Claude Code workflows

### 6.4 Next Steps

1. **Prototype** basic PostToolUse hook with fixed delays
2. **Validate** hook execution overhead in real scenarios
3. **Test** API integration with cached credentials
4. **Design** state file schema with versioning
5. **Implement** MVP with core throttling only

---

## Conclusion

The Claude Pace Maker epic is **technically feasible** and architecturally sound. The recommended pure hooks approach leverages Claude Code's native capabilities while minimizing complexity. The existing `claude-usage-reporting` codebase provides proven patterns for API integration and state management.

Key technical challenges around performance, state management, and API reliability have clear mitigation strategies. The phased implementation approach allows for iterative validation and refinement.

**Recommendation**: Proceed with implementation using the pure hooks architecture with bash scripting for hooks and Python for the dashboard.