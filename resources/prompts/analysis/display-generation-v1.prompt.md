---
id: display-generation-v1
name: React Component Display Generation
version: 1.0.0
category: analysis
tags: [ui, react, component, visualization]

prompt:
  source: markdown

context_flexibility: high
context:
  default_pack: static-minimal

always_on: []

vars:
  report_content: { type: string, required: true, example: "Analysis results..." }

execution:
  mode: backend-task
  result_type: text
  result_title: "Report Display Component"
  model_preferences: [claude-sonnet-4-5-20250929, gpt-4o]
---

# React Component Display Generation

I have this generated report based on a chat analysis.

Could you build me a custom React component to display this report in an engaging and visually appealing way?

REPORT CONTENT:
{report_content}

Please create a React functional component that presents this report. The component should:
- Be defined as a const, e.g., const ReportDisplay = () => {{ ... }}
- Use React hooks like useState and useEffect if needed, which are available in the scope.
- Use shadcn/ui UI components for a consistent look and feel.
- Use icons from LucideIcons to enhance visual interest.
- Use Recharts for any charts if the report includes data that can be visualized.
- Include all styles inline using the 'style' attribute or Tailwind CSS classes.
- DO NOT MAKE UP ANY NEW DATA; use only the information provided in the report.

CRITICAL RESPONSIVE DESIGN REQUIREMENTS:
- The component MUST be designed for a narrow container (320px minimum width)
- Start with mobile-first design principles
- Use 'w-full' on ALL container elements
- Never use fixed pixel widths except for small UI elements
- Use 'max-w-full overflow-hidden' on the root container
- Use 'space-y-4' for vertical spacing between sections
- For any horizontal layouts, use 'flex flex-col sm:flex-row' to stack on mobile
- Use 'text-sm' as the default text size, 'text-xs' for secondary text
- For any cards or sections, use 'p-3' or 'p-4' maximum padding
- Break long words with 'break-words' class
- Hide non-essential elements on mobile using 'hidden sm:block'

EXAMPLE STRUCTURE:
```jsx
const ReportDisplay = () => {{
  return (
    <div className="w-full max-w-full overflow-hidden space-y-4">
      <div className="bg-white rounded-lg shadow-sm p-3">
        <h2 className="text-lg font-semibold mb-2">Title</h2>
        <p className="text-sm text-gray-600 break-words">Content...</p>
      </div>
      {{/* More sections */}}
    </div>
  );
}};
```

Include necessary import statements at the top of the file to indicate which libraries are used.
Return ONLY the React component code without any additional explanation.

