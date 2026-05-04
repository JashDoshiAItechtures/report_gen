"""DSPy signatures for AI-powered chart modification via natural language instructions."""

import dspy


class ChartModification(dspy.Signature):
    """You are an expert data analyst who modifies chart SQL queries and configurations
    based on natural language instructions. Modify ONLY what is asked — preserve everything else.

    ══════════════════════════════════════════════════════════════
    SQL RULES (apply only when SQL needs to change)
    ══════════════════════════════════════════════════════════════
    - Write SELECT queries ONLY. Never DML.
    - The FIRST column becomes the chart label / x-axis.
    - Always return at least 2 columns: one label and one numeric value.
    - Use clear column aliases (e.g. AS revenue, AS month, AS category).
    - Top-N:      ORDER BY <value_col> DESC LIMIT N
    - Monthly:    DATE_TRUNC('month', order_date) AS month
    - Yearly:     EXTRACT(YEAR FROM order_date)::int AS year
    - Filters:    Add WHERE clause with the appropriate column and value.
    - Grouping:   Add GROUP BY the appropriate dimension column.
    - Preserve existing valid JOINs unless the data source must change.
    - Default LIMIT 20 when query could return many rows.

    ══════════════════════════════════════════════════════════════
    CHART TYPES
    ══════════════════════════════════════════════════════════════
    bar | horizontalBar | line | area | pie | doughnut | stackedBar

    ══════════════════════════════════════════════════════════════
    COLOR SCHEMES
    ══════════════════════════════════════════════════════════════
    blues | greens | purples | oranges | mixed | gradient
    Use these when the user asks to change chart color/colour/palette/theme.
    Example: "change to blue" → blues, "make it purple" → purples,
    "use orange" → oranges, "mixed colors" → mixed, "gradient" → gradient.

    ══════════════════════════════════════════════════════════════
    WHEN TO CHANGE SQL vs TYPE ONLY
    ══════════════════════════════════════════════════════════════
    Change SQL:   change axes, top-N, filter by value, add grouping, change metric
    Type only:    "convert to line", "make it a pie chart", "show as bar"
    Color only:   "change color to blue", "use green palette", "make it purple"

    ══════════════════════════════════════════════════════════════
    OUTPUT RULES
    ══════════════════════════════════════════════════════════════
    - For each output field: write the new value, OR write exactly UNCHANGED.
    - new_sql must be a complete valid SELECT or exactly UNCHANGED.
    - confidence: high=clear instruction, medium=somewhat clear, low=too vague.
    - If confidence=low, set clarification_needed to a specific question.
      Otherwise write exactly NONE.
    """

    instruction = dspy.InputField(
        desc="User's natural language instruction to modify the chart"
    )
    current_sql = dspy.InputField(
        desc="Current SQL query powering the chart"
    )
    current_chart_type = dspy.InputField(
        desc="Current chart type: bar | horizontalBar | line | area | pie | doughnut | stackedBar"
    )
    current_title = dspy.InputField(
        desc="Current chart title"
    )
    current_x_label = dspy.InputField(
        desc="Current x-axis label (may be empty)"
    )
    current_y_label = dspy.InputField(
        desc="Current y-axis label (may be empty)"
    )
    schema_info = dspy.InputField(
        desc="Full database schema with tables and columns"
    )
    modification_history = dspy.InputField(
        desc="Semicolon-separated list of previous instructions applied to this chart. Empty if none."
    )

    new_sql = dspy.OutputField(
        desc="Modified SQL query, or exactly UNCHANGED if SQL does not need modification"
    )
    new_chart_type = dspy.OutputField(
        desc="New chart type, or exactly UNCHANGED"
    )
    new_title = dspy.OutputField(
        desc="New chart title, or exactly UNCHANGED"
    )
    new_x_label = dspy.OutputField(
        desc="New x-axis label, or exactly UNCHANGED"
    )
    new_y_label = dspy.OutputField(
        desc="New y-axis label, or exactly UNCHANGED"
    )
    new_color_scheme = dspy.OutputField(
        desc="New color scheme (blues|greens|purples|oranges|mixed|gradient), or exactly UNCHANGED"
    )
    explanation = dspy.OutputField(
        desc="One clear sentence describing what was changed and why"
    )
    confidence = dspy.OutputField(
        desc="high | medium | low"
    )
    clarification_needed = dspy.OutputField(
        desc="Specific clarification question if confidence=low, else exactly NONE"
    )


class PromptRewriter(dspy.Signature):
    """You rewrite vague chart modification requests into specific, actionable instructions.

    When a user gives a vague instruction like 'make it better' or 'fix it',
    infer what they likely mean based on the chart context and provide concrete options.
    """

    vague_instruction = dspy.InputField(
        desc="The vague or unclear modification instruction from the user"
    )
    chart_context = dspy.InputField(
        desc="Current chart title, type, and data column names"
    )

    rewritten_instruction = dspy.OutputField(
        desc="Your best guess at what the user means, written as a specific actionable instruction"
    )
    options = dspy.OutputField(
        desc='JSON array of 3-4 specific interpretations, '
             'e.g. ["Show top 10 items", "Convert to line chart", "Filter last 3 months", "Group by category"]'
    )
