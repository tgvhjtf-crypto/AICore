// AICore 本地版：不需要服务器，所有逻辑和数据都在本机运行
(()=>{
const LIMIT=50,MAXSUB=3,MAXTOK=2000,PLANTOK=700;
const SEED=[
["主控AI","manager","你是主控AI，负责理解并完善用户需求、按专家擅长分配任务、汇总并检查结果。回答简洁。","openai","https://generativelanguage.googleapis.com/v1beta/openai","gemini-2.0-flash"],
["GPT","expert","你是综合分析专家，擅长复杂任务。","openai","https://api.openai.com/v1","gpt-4o"],
["Claude","expert","你是代码分析与程序开发专家。","anthropic","https://api.anthropic.com","claude-sonnet-5-5"],
["Gemini","expert","你是信息分析与多模态专家。","openai","https://generativelanguage.googleapis.com/v1beta/openai","gemini-2.0-flash"],
["DeepSeek","expert","你是逻辑推理与代码辅助专家。","openai","https://api.deepseek.com","deepseek-chat"],
["Grok","expert","你是信息分析与观点讨论专家。","openai","https://api.x.ai/v1","grok-3"]];
let D;try{D=JSON.parse(localStorage.getItem('aicore'))}catch(e){}
if(!D)D={seq:100,agents:SEED.map((s,i)=>({id:i+1,name:s[0],role:s[1],prompt:s[2],kind:s[3],base_url:s[4],model:s[5],api_key:'',enabled:0})),tasks:[],memories:[],usage:{}};
const save=()=>localStorage.setItem('aicore',JSON.stringify(D));
const nid=()=>++D.seq, now=()=>Date.now()/1000;
D.tasks.forEach(t=>{if(t.status=='running'||t.status=='planning'){t.status='failed';t.result='应用被关闭，任务中断，请重试'}});save();

async function chat(a,system,user,max){
  if(!a.api_key)throw new Error('未配置 API Key');
  const day=new Date().toISOString().slice(0,10);D.usage[day]=(D.usage[day]||0)+1;save();
  if(D.usage[day]>LIMIT)throw new Error('已超过每日调用上限');
  const base=a.base_url.replace(/\/$/,'');let last;
  for(let i=0;i<2;i++){
    try{
      let r;
      if(a.kind=='anthropic')r=await fetch(base+'/v1/messages',{method:'POST',headers:{'content-type':'application/json','x-api-key':a.api_key,'anthropic-version':'2023-06-01','anthropic-dangerous-direct-browser-access':'true'},body:JSON.stringify({model:a.model,max_tokens:max,system,messages:[{role:'user',content:user}]})});
      else r=await fetch(base+'/chat/completions',{method:'POST',headers:{'content-type':'application/json','authorization':'Bearer '+a.api_key},body:JSON.stringify({model:a.model,max_tokens:max,messages:[{role:'system',content:system},{role:'user',content:user}]})});
      if(!r.ok){const tx=await r.text();throw new Error('HTTP '+r.status+'：'+(tx.trim().startsWith('<')?'服务器返回了网页而不是接口数据，可能是 API 地址不对，或被 Cloudflare 拦截':tx.slice(0,120)))}
      const raw=await r.text();let j;try{j=JSON.parse(raw)}catch(e){throw new Error('返回内容不是接口数据：'+raw.slice(0,60).replace(/</g,'‹'))}
      return a.kind=='anthropic'?j.content.map(b=>b.text||'').join(''):j.choices[0].message.content;
    }catch(e){last=e}
  }
  throw new Error('调用失败：'+last.message);
}
const say=(t,agent,kind,content)=>{t.messages.push({id:nid(),agent,kind,content,ts:now()});save()};
const fail=(t,e)=>{say(t,'系统','error',e.message);t.status='failed';t.result=e.message;save()};
function team(){
  const on=D.agents.filter(a=>a.enabled),mgr=on.find(a=>a.role=='manager'),experts={};
  on.filter(a=>a.role=='expert').forEach(a=>experts[a.name]=a);
  if(!mgr||!Object.keys(experts).length)throw new Error('请先启用一个主控AI和至少一个专家');
  return{mgr,experts};
}
function parse(text,experts,fb){
  try{const d=JSON.parse(text.match(/\{[\s\S]*\}/)[0]);
    const plan=(d.plan||[]).filter(p=>experts[p.agent]).slice(0,MAXSUB);
    if(plan.length)return[String(d.refined||fb),plan];}catch(e){}
  return[fb,[{agent:Object.keys(experts)[0],instruction:fb}]];
}
async function planTask(t){
  try{
    t.status='planning';t.messages=[];save();
    const{mgr,experts}=team();
    const ctx=D.memories.slice(-5).map(m=>'- '+m.content.slice(0,200)).join('\n')||'（无）';
    const tm=Object.values(experts).map(a=>`- ${a.name}：${a.prompt.slice(0,80)}（模型 ${a.model}）`).join('\n');
    const out=await chat(mgr,mgr.prompt,`共享记忆：\n${ctx}\n\n用户原话：${t.title}\n\n专家团队：\n${tm}\n\n请：1) 理解并完善用户需求，写成清晰具体的任务描述；2) 按专家擅长拆分并分配，能由一位专家完成就只分配一位，最多${MAXSUB}位，避免重复劳动。\n只输出JSON：{"refined":"完善后的需求","plan":[{"agent":"专家名","instruction":"该专家的子任务"}]}`,PLANTOK);
    [t.refined,t.plan]=parse(out,experts,t.title);
    t.status='awaiting';
    say(t,mgr.name,'plan','已完善需求并完成分配。确认之前不会调用任何专家，不产生专家费用。');
  }catch(e){fail(t,e)}
}
async function executeTask(t){
  try{
    t.status='running';t.subtasks=[];t.messages=t.messages.filter(m=>m.kind=='plan');save();
    const{mgr,experts}=team();
    const plan=t.plan.filter(p=>experts[p.agent]);
    if(!plan.length)throw new Error('没有可执行的专家，请检查专家是否已启用');
    const goal=t.refined||t.title;
    const subs=plan.map(p=>({id:nid(),agent:p.agent,instruction:p.instruction,status:'pending',output:''}));
    t.subtasks=subs;save();
    const res=(await Promise.all(subs.map(async s=>{
      const a=experts[s.agent];s.status='running';save();
      try{const out=await chat(a,a.prompt,`总任务：${goal}\n你的子任务：${s.instruction}`,MAXTOK);s.status='done';s.output=out;say(t,a.name,'work',out);return[a.name,out]}
      catch(e){s.status='failed';s.output=e.message;say(t,a.name,'error',e.message)}
    }))).filter(Boolean);
    if(!res.length)throw new Error('所有专家都执行失败');
    const final=res.length==1?res[0][1]:await chat(mgr,mgr.prompt,`用户任务：${goal}\n专家结果：\n${res.map(r=>`【${r[0]}】${r[1]}`).join('\n\n')}\n\n请检查质量并整合为一份最终答案。`,MAXTOK);
    say(t,mgr.name,'final',final);t.status='done';t.result=final;
    D.memories.push({id:nid(),content:`任务「${t.title.slice(0,40)}」结论：${final.slice(0,400)}`,source:'task',ts:now()});save();
  }catch(e){fail(t,e)}
}
const pub=a=>{const{api_key,...r}=a;return{...r,has_key:!!api_key}};
window.localApi=async(p,o={})=>{
  const m=o.method||'GET',b=o.body||{},[path,qs]=p.split('?');let x;
  if(path=='/agents'){
    if(m=='GET')return D.agents.map(pub);
    D.agents.push({id:nid(),name:'新专家',role:'expert',prompt:'你是一名专家。',kind:'openai',base_url:'https://api.openai.com/v1',model:'gpt-4o',api_key:'',enabled:0});save();return{ok:1};
  }
  if(x=path.match(/^\/agents\/(\d+)(\/test)?$/)){
    const a=D.agents.find(a=>a.id==x[1]);if(!a)throw new Error('专家不存在');
    if(x[2]){try{await chat(a,'你是测试助手。','回复OK',20);return{ok:true}}catch(e){return{ok:false,error:e.message}}}
    if(m=='DELETE'){D.agents=D.agents.filter(q=>q!==a);save();return{ok:1}}
    for(const k of['name','role','prompt','kind','base_url','model'])if(b[k]!=null)a[k]=b[k];
    if(b.api_key)a.api_key=b.api_key;if(b.enabled!=null)a.enabled=b.enabled?1:0;save();return{ok:1};
  }
  if(path=='/tasks'){
    if(m=='GET')return D.tasks.slice().reverse().slice(0,50).map(({id,title,status,created})=>({id,title,status,created}));
    const t={id:nid(),title:String(b.title).trim().slice(0,2000),status:'pending',refined:'',plan:[],result:'',created:now(),subtasks:[],messages:[]};
    D.tasks.push(t);save();planTask(t);return{id:t.id};
  }
  if(x=path.match(/^\/tasks\/(\d+)(?:\/(\w+))?$/)){
    const t=D.tasks.find(t=>t.id==x[1]);if(!t)throw new Error('任务不存在');
    if(!x[2])return JSON.parse(JSON.stringify(t));
    if(x[2]=='retry'){(t.plan.length?executeTask:planTask)(t);return{ok:1}}
    if(x[2]=='cancel'){if(t.status=='awaiting'||t.status=='failed'){t.status='cancelled';save()}return{ok:1}}
    if(x[2]=='confirm'){
      if(t.status!='awaiting')throw new Error('任务不在待确认状态');
      const sel=b.picks.filter(k=>t.plan[k]).map(k=>t.plan[k]);
      if(!sel.length)throw new Error('至少选择一位专家');
      t.refined=(b.refined||'').trim()||t.refined;t.plan=sel;save();executeTask(t);return{ok:1};
    }
  }
  if(path=='/memories'){
    if(m=='GET'){const q=new URLSearchParams(qs||'').get('q')||'';return D.memories.filter(k=>k.content.includes(q)).reverse().slice(0,100)}
    D.memories.push({id:nid(),content:b.content,source:'user',ts:now()});save();return{ok:1};
  }
  if(x=path.match(/^\/memories\/(\d+)$/)){D.memories=D.memories.filter(k=>k.id!=x[1]);save();return{ok:1}}
  throw new Error('未知请求 '+p);
};
})();
