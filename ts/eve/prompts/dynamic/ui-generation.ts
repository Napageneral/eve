// UI generation prompts
// Dynamic TypeScript functions for Eve

export function buildUIRenderSystem(vars: { widthPx?: number }): string {
  const w = Math.max(240, Math.min(1200, Number(vars.widthPx || 360)));
  return `You generate small, self-contained React UI panels (TSX) to run inside a sandboxed iframe.
Constraints:
- No imports. Do not reference window, document, fetch.
- You have React, and a scope with: Button, Card, Badge, LucideIcons (e.g. <Sparkles />), and emit(), context().
- Export nothing. Define a component and call render(<Component />) at the end.
- Mobile-first: Fit within width ≈ ${w}px. Never overflow horizontally. Use responsive layout (grid or wrap). Truncate long text with ellipsis.
- List density: Show up to 2 actions, one per row.
- Panel chrome: Give the outer panel a colorful background gradient (prefer blue/indigo; red/rose acceptable). Use symmetric padding of 12px (top AND bottom) so content never touches edges. Use 8px gaps between items similar to our standard Suggestions. Do NOT use bottom padding of 0 or 8px.
- Style inline or with available components; use rounded shapes, soft shadows, subtle borders, small gradients for accents.
- Provide hover/press affordances using local component state (onMouseEnter/Leave), not CSS :hover.
- Do NOT render any dropdowns, popovers, hover-cards, or tooltips. Keep everything inline.
- Use LucideIcons by name; if unknown, default to <Sparkles />.
- Each action triggers emit({ type: 'action', id: '<your_action_id>' }).

Example pattern (reference only, do not import anything):

function Chip({ item }){
  const map = {
    blue:{ bg:'#eef2ff', text:'#1e3a8a', br:'#c7d2fe', grad:'linear-gradient(135deg,#eef2ff,#e0e7ff)' },
    green:{ bg:'#ecfdf5', text:'#065f46', br:'#a7f3d0', grad:'linear-gradient(135deg,#ecfdf5,#d1fae5)' },
    purple:{ bg:'#f5f3ff', text:'#5b21b6', br:'#ddd6fe', grad:'linear-gradient(135deg,#f5f3ff,#ede9fe)' },
    yellow:{ bg:'#fffbeb', text:'#92400e', br:'#fde68a', grad:'linear-gradient(135deg,#fffbeb,#fef3c7)' },
    gray:{ bg:'#f5f5f5', text:'#374151', br:'#e5e7eb', grad:'linear-gradient(135deg,#f5f5f5,#e5e7eb)' }
  };
  const [hover, setHover] = React.useState(false);
  const c = map[item.color || 'gray'];
  const Icon = (LucideIcons as any)[item.icon || 'Sparkles'] || (LucideIcons as any).Sparkles;
  return (
    <div onMouseEnter={()=>setHover(true)} onMouseLeave={()=>setHover(false)}>
      <button onClick={()=>emit({ type:'action', id:item.id })}
        style={{ display:'inline-flex', alignItems:'center', gap:8, width:'100%', padding:'8px 12px', borderRadius:12,
          border:'1px solid #e5e7eb', color:'#111', background:hover?'#f9fafb':'#fff',
          boxShadow:hover?'0 6px 14px rgba(0,0,0,0.06)':'0 1px 2px rgba(0,0,0,0.04)', transform:hover?'translateY(-1px)':'none', minHeight:32, fontSize:13 }}>
        <Icon className="h-3.5 w-3.5" style={{ color: c.text }} />
        <span style={{ fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'normal' }}>{item.title}</span>
      </button>
    </div>
  );
}

function Panel(){
  const items = (INPUT_SUGGESTIONS || []).slice(0,2); // [{ id, title, icon?, color? }]
  return (
    <div style={{ width:${w}, maxWidth:'100%', padding:'12px', borderRadius:0, background:'linear-gradient(180deg,#eaf2ff,#dbeafe)', display:'grid', gridTemplateColumns:'1fr', gap:8, alignItems:'stretch' }}>
      {items.map((s)=> (<Chip key={s.id} item={s} />))}
    </div>
  );
}
render(<Panel/>);

Output: Only TSX code that can be executed directly.`;
}

export function buildUIRenderUserPrompt(vars: {
  suggestions: Array<{ id: string; title: string; icon?: string; color?: string }>;
  analysisSummary?: string | null;
}): string {
  const lines: string[] = [];
  lines.push('Create a compact, helpful action board for the active conversation.');
  lines.push('Always return runnable TSX per the system instructions. Use icons/colors when provided.');
  if (vars.analysisSummary) {
    lines.push('Focus your ideas on this latest analysis summary:');
    lines.push(String(vars.analysisSummary));
  }
  lines.push('Replace INPUT_SUGGESTIONS in the example with this array:');
  lines.push(JSON.stringify(vars.suggestions || []));
  return lines.join('\n');
}

