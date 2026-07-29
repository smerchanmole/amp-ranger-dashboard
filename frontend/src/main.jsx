import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AreaChart, Area, BarChart, Bar, CartesianGrid, Cell, PieChart, Pie, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {Activity, AlertTriangle, CheckCircle2, Clock3, FileClock, RefreshCw, Send, ShieldCheck, Users, XCircle} from 'lucide-react';
import 'leaflet/dist/leaflet.css';
import {CircleMarker, MapContainer, Popup, TileLayer, useMap} from 'react-leaflet';
import rangerHero from '../../topo_ranger.PNG';
import './styles.css';

const API = import.meta.env.VITE_API_URL || '/api';
const COLORS = ['#28c7a9','#49a9e8','#78d7d1','#8d7de8','#a9d76e','#f3b35c','#ef7180'];

async function request(path, options) {
  const response = await fetch(`${API}${path}`, {...options, credentials:'include'});
  if (!response.ok) {
    if(response.status===401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('auth-expired'));
    const error=new Error((await response.json()).detail || 'Error de conexión'); error.status=response.status; throw error;
  }
  return response.json();
}

const number = value => Number(value || 0).toLocaleString('es-ES');
const formatDate = value => value ? new Intl.DateTimeFormat('es-ES', {dateStyle:'short',timeStyle:'medium'}).format(new Date(value)) : '—';

function Kpi({label, value, sub, tone='green', icon: Icon}) {
  return <article className={`kpi ${tone}`}><div className="kpi-head"><span>{label}</span><Icon size={18}/></div><strong>{number(value)}</strong><small>{sub}</small></article>;
}

function Panel({title, subtitle, children, wide=false, full=false}) {
  return <section className={`panel ${wide?'wide':''} ${full?'full':''}`}><header><div><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div></header>{children}</section>;
}

function Empty({children='Sin datos para este periodo'}) { return <div className="empty">{children}</div>; }

function ResourceGallery({title, subtitle, groups=[]}) {
  return <section className="resource-zone">
    <header className="section-heading"><div><span className="section-dot"/><h2>{title}</h2></div><p>{subtitle}</p></header>
    {groups.length ? <div className="resource-widget-grid">{groups.map(group=>
      <article className="resource-widget" key={group.name}>
        <header><div className="entity-avatar">{group.name.slice(0,2).toUpperCase()}</div><div><h3>{group.name}</h3><small>{number(group.total)} accesos</small></div></header>
        <div className="resource-widget-body">
          <div className="donut-shell">
            <ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={group.resources} dataKey="value" innerRadius="62%" outerRadius="88%" paddingAngle={2} stroke="rgba(255,255,255,.9)" strokeWidth={2}>{group.resources.map((_,index)=><Cell key={index} fill={COLORS[index%COLORS.length]}/>)}</Pie><Tooltip formatter={(value,name)=>[number(value),name]}/></PieChart></ResponsiveContainer>
            <div className="donut-total"><strong>{number(group.total)}</strong><span>total</span></div>
          </div>
          <ol className="resource-mini-list">{group.resources.map((resource,index)=><li key={`${resource.service}-${resource.raw}-${index}`} title={resource.raw || resource.context}><i style={{background:COLORS[index%COLORS.length]}}/><span><b>{resource.service ? `${resource.service} · ${resource.name}` : resource.name}</b><small>{resource.context}</small></span><strong>{number(resource.value)}</strong></li>)}</ol>
        </div>
      </article>
    )}</div>:<Empty/>}
  </section>;
}

function IdentityImpact({items=[], mode='access'}) {
  if (!items.length) return <Empty/>;
  const total=items.reduce((sum,item)=>sum+item.value,0);
  const max=Math.max(...items.map(item=>item.value),1);
  const isRisk=mode==='risk';
  const topShare=total ? items[0].value*100/total : 0;
  const severity=ratio=>ratio>=.75?'Crítica':ratio>=.45?'Alta':ratio>=.2?'Media':'Baja';
  return <div className={`identity-impact ${isRisk?'risk':''}`}>
    <div className="identity-summary">
      <div><span>{isRisk?'Usuarios afectados':'Identidades visibles'}</span><strong>{number(items.length)}</strong></div>
      <div><span>{isRisk?'Denegaciones representadas':'Accesos representados'}</span><strong>{number(total)}</strong></div>
      <div><span>Concentración principal</span><strong>{topShare.toFixed(1)}%</strong></div>
    </div>
    <div className="identity-list">{items.map((item,index)=>{
      const ratio=item.value/max;
      const share=total ? item.value*100/total : 0;
      const hue=isRisk ? 355-22*ratio : 171+31*ratio;
      const endHue=isRisk ? 337-10*ratio : 194+18*ratio;
      return <article className="identity-row" key={item.name} style={{'--strength':`${Math.max(3,ratio*100)}%`,'--bar-start':`hsl(${hue} 76% ${isRisk?65:48}%)`,'--bar-end':`hsl(${endHue} 78% ${isRisk?58:55}%)`}}>
        <div className="identity-rank">{String(index+1).padStart(2,'0')}</div>
        <div className="identity-name"><span>{item.name}</span><small>{isRisk?`${severity(ratio)} prioridad relativa`:`${share.toFixed(1)}% de la actividad`}</small></div>
        <div className="identity-track"><i/><span/></div>
        <div className="identity-value"><strong>{number(item.value)}</strong><small>{share.toFixed(1)}%</small></div>
      </article>;
    })}</div>
  </div>;
}

function MapViewport({points}) {
  // Gobierno de localización: el encuadre se deriva de la evidencia y añade
  // un margen físico aproximado; no presupone que todos los accesos son locales.
  const map = useMap();
  useEffect(()=>{
    if (!points?.length) return;
    const lats=points.map(p=>p.lat), lngs=points.map(p=>p.lng);
    const centerLat=(Math.min(...lats)+Math.max(...lats))/2;
    const latMargin=5/111;
    const lngMargin=5/(111*Math.max(.2,Math.cos(centerLat*Math.PI/180)));
    map.fitBounds([
      [Math.min(...lats)-latMargin,Math.min(...lngs)-lngMargin],
      [Math.max(...lats)+latMargin,Math.max(...lngs)+lngMargin],
    ],{padding:[24,24],maxZoom:13});
  },[map,points]);
  return null;
}

function DataTable({rows, kind='', limit=100}) {
  if (!rows?.length) return <Empty/>;
  const columns = Object.keys(rows[0]).filter(key => !['raw','allowed'].includes(key));
  const labels = {date:'Fecha y hora',user:'Usuario',ip:'IP',service:'Servicio',operation:'Operación',resource:'Recurso',context:'Base de datos / ruta',name:'Recurso',value:'Total accesos'};
  return <div className={`table-wrap ${kind}`}><table><thead><tr>{columns.map(key=><th key={key}>{labels[key] || key.replaceAll('_',' ')}</th>)}</tr></thead><tbody>{rows.slice(0,limit).map((row,index)=><tr key={index}>{columns.map(key=><td key={key} title={String(row[key] ?? '')}>{key==='date'?formatDate(row[key]):key==='value'||key==='accesos'||key==='denegaciones'?number(row[key]):String(row[key] ?? '—')}</td>)}</tr>)}</tbody></table></div>;
}

function Dashboard({data, map}) {
  // La UI consume agregados gobernados. No recalcula decisiones Ranger ni
  // accede a credenciales, manteniendo una única definición de cada KPI.
  if (!data) return <div className="loading"><RefreshCw className="spin"/> Cargando telemetría de Ranger…</div>;
  const s=data.summary;
  const access=[{name:'Permitidos',value:s.allowed},{name:'Denegados',value:s.denied}];
  return <>
    <div className="kpis six access-kpis">
      <Kpi label="OK · Última hora" value={s.hourAllowed} sub="Accesos permitidos en 60 minutos" icon={Clock3}/>
      <Kpi label="OK · Hoy" value={s.todayAllowed} sub="Accesos permitidos desde las 00:00 UTC" icon={CheckCircle2}/>
      <Kpi label="OK · Muestra" value={s.allowed} sub={`${(100-s.denialRate).toFixed(1)}% de ${number(s.total)} accesos`} icon={Activity}/>
      <Kpi label="KO · Última hora" value={s.hourDenied} sub="Accesos denegados en 60 minutos" tone="red" icon={Clock3}/>
      <Kpi label="KO · Hoy" value={s.todayDenied} sub="Accesos denegados desde las 00:00 UTC" tone="red" icon={XCircle}/>
      <Kpi label="KO · Muestra" value={s.denied} sub={`${s.denialRate}% de denegación`} tone="red" icon={AlertTriangle}/>
    </div>
    <div className="grid">
      <Panel title="Evolución de accesos" subtitle="Permitidos y denegados por día" wide>{data.timeline.length?<ResponsiveContainer width="100%" height={280}><AreaChart data={data.timeline}><CartesianGrid strokeDasharray="3 3" stroke="#d7e8ec"/><XAxis dataKey="date" tick={{fill:'#71849a'}}/><YAxis tick={{fill:'#71849a'}}/><Tooltip contentStyle={{background:'rgba(255,255,255,.96)',border:'1px solid #cce2e6',color:'#173249',borderRadius:12}}/><Area type="monotone" dataKey="allowed" name="Permitidos" stroke="#28c7a9" strokeWidth={3} fill="#28c7a922"/><Area type="monotone" dataKey="denied" name="Denegados" stroke="#ef7180" strokeWidth={3} fill="#ef718011"/></AreaChart></ResponsiveContainer>:<Empty/>}</Panel>
      <Panel title="Resultado" subtitle="Distribución de decisiones"><ResponsiveContainer width="100%" height={280}><PieChart><Pie data={access} dataKey="value" innerRadius={70} outerRadius={100} paddingAngle={3}>{access.map((_,i)=><Cell key={i} fill={[COLORS[0],COLORS[3]][i]}/>)}</Pie><Tooltip/></PieChart></ResponsiveContainer><div className="legend"><i className="allow"/>Permitidos <i className="deny"/>Denegados</div></Panel>
      <ResourceGallery title="Recursos más usados por servicio" subtitle="Un toro por servicio muestra la distribución de sus activos más consultados" groups={data.resourcesByService}/>
      <ResourceGallery title="Recursos más usados por usuario" subtitle="La misma visión desde la identidad: qué recursos concentra cada usuario" groups={data.resourcesByUser}/>
      <Panel title="Actividad por identidad" subtitle="Ranking proporcional de accesos y concentración de uso"><IdentityImpact items={data.accessesByUser}/></Panel>
      <Panel title="Identidades con mayor riesgo" subtitle="Denegaciones priorizadas por intensidad relativa"><IdentityImpact items={data.topDeniedUsers} mode="risk"/></Panel>
      <Panel title="Recursos utilizados" subtitle="Nombre, base de datos o ruta y total de accesos de los últimos 7 días" full><DataTable rows={data.resourceTable}/></Panel>
      <Panel title="Origen geográfico" subtitle="Encuadre automático con unos 5 km de margen; las IP locales se agrupan en Embajadores 181" wide>{map?.databaseReady?<MapContainer center={[40.3912,-3.69233]} zoom={12} className="map"><MapViewport points={map.points}/><TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"/>{map.points.map(p=><CircleMarker key={p.ip} center={[p.lat,p.lng]} radius={Math.max(7,Math.min(26,Math.sqrt(p.count)))} pathOptions={{color:p.local?'#ffb94a':'#22d3b6',fillColor:p.local?'#ffb94a':'#22d3b6',fillOpacity:.7}}><Popup><b>{p.city || p.country}</b><br/>{p.ip}: {p.count} accesos</Popup></CircleMarker>)}</MapContainer>:<Empty>El índice geográfico aún no está construido.</Empty>}</Panel>
      <Panel title="IPs con más denegaciones" subtitle="Origen de eventos bloqueados"><div className="rank-list">{data.topDeniedIps.map((x,i)=><div key={x.name}><span><em>{i+1}</em>{x.name}</span><strong>{number(x.value)}</strong></div>)}</div></Panel>
      <Panel title="Últimos 100 accesos permitidos" subtitle="Eventos OK más recientes de la muestra" full><DataTable rows={data.recentAllowed} kind="ok"/></Panel>
      <Panel title="Últimos 100 accesos denegados" subtitle="Eventos KO que requieren atención" full><DataTable rows={data.recentDenied} kind="ko"/></Panel>
    </div>
  </>;
}

function ChatChart({chart}) {
  if (!chart?.data?.length) return null;
  return <div className="chat-chart"><strong>{chart.title}</strong><ResponsiveContainer width="100%" height={Math.min(300,Math.max(150,chart.data.length*34))}><BarChart data={chart.data.slice(0,10)} layout="vertical" margin={{left:8,right:18}}><XAxis type="number" tick={{fill:'#71849a'}} allowDecimals={false}/><YAxis type="category" dataKey="name" width={115} tick={{fill:'#21364a'}}/><Tooltip/><Bar dataKey="value" fill="#28c7a9" radius={[0,7,7,0]}/></BarChart></ResponsiveContainer></div>;
}

function Chat({period, excludeInternal, sampleSize, model}) {
  // El alcance visual (periodo, identidades y muestra) también viaja al chat;
  // así una respuesta nunca mezcla universos distintos a los del dashboard.
  const [messages,setMessages]=useState([{role:'bot',text:'Soy el analista de seguridad de Apache Ranger. Consulto auditorías de solo lectura en Solr mediante Kerberos y políticas mediante la API GET de Ranger. Puedo cruzar accesos, usuarios externos, servicios, operaciones, recursos e IP, y responder con texto, tabla o gráfica. Nunca modifico Ranger.'}]);
  const [text,setText]=useState(''); const [busy,setBusy]=useState(false);
  const send=async()=>{if(!text.trim()||busy)return;const q=text.trim();setMessages(m=>[...m,{role:'user',text:q}]);setText('');setBusy(true);try{const r=await request('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,period,exclude_internal:excludeInternal,sample_size:sampleSize,model})});setMessages(m=>[...m,{role:'bot',text:r.answer,table:r.table,chart:r.chart,model:r.model,warning:r.gatewayWarning}]);}catch(e){setMessages(m=>[...m,{role:'error',text:e.message}]);}finally{setBusy(false)}};
  return <section className="chat"><div className="chat-title"><div className="bot-icon"><ShieldCheck/></div><div><h2>Analista Ranger</h2><p><span/> AI Gateway · {model} · Solo lectura</p></div></div><div className="messages">{messages.map((m,i)=><div key={i} className={`message ${m.role}`}><p>{m.text}</p>{m.model&&<small className="model-badge">Modelo: {m.model}{m.warning?' · respuesta determinista de respaldo':''}</small>}{m.chart&&<ChatChart chart={m.chart}/>} {m.table?.length>0&&<DataTable rows={m.table} limit={20}/>}</div>)}{busy&&<div className="message bot dots">Consultando {model} a través de AI Gateway…</div>}</div><div className="suggestions">{['¿Qué APIs puedes llamar?','Dime los accesos de la última hora','Usuarios que han accedido y a qué servicio','¿Qué recursos fueron los más solicitados?'].map(q=><button key={q} onClick={()=>setText(q)}>{q}</button>)}</div><div className="composer"><input value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>e.key==='Enter'&&send()} placeholder="Pregunta sobre accesos, usuarios, servicios, recursos o políticas…"/><button onClick={send} disabled={busy}><Send size={18}/></button></div></section>;
}

function Logs({close}) {const [items,setItems]=useState([]);useEffect(()=>{request('/logs').then(x=>setItems(x.items))},[]);return <div className="modal"><div className="log-panel"><header><h2>Registro de actividad</h2><button onClick={close}>Cerrar</button></header>{items.length?items.map((x,i)=><pre key={i}>{JSON.stringify(x,null,2)}</pre>):<Empty>No hay consultas registradas todavía.</Empty>}</div></div>;}

function LoginScreen({onLogin}) {
  const [username,setUsername]=useState(''); const [password,setPassword]=useState('');
  const [error,setError]=useState(''); const [busy,setBusy]=useState(false);
  const submit=async event=>{event.preventDefault();setBusy(true);setError('');try{const session=await request('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});setPassword('');onLogin(session);}catch(err){setError(err.message);}finally{setBusy(false)}};
  return <div className="login-page"><section className="login-card"><img src={rangerHero} alt="Ranger Intelligence"/><div className="login-content"><div className="brand login-brand"><div><ShieldCheck/></div><span>RANGER<strong>INTELLIGENCE</strong></span></div><p className="eyebrow">GOBIERNO Y SEGURIDAD</p><h1>Acceso al centro de control</h1><p className="login-copy">Identifícate para consultar la evidencia de Apache Ranger. La sesión se protege mediante una cookie segura.</p><form onSubmit={submit}><label>Usuario<input autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required autoFocus/></label><label>Contraseña<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required/></label>{error&&<div className="login-error"><AlertTriangle size={17}/>{error}</div>}<button type="submit" disabled={busy}>{busy?'Validando…':'Entrar de forma segura'}</button></form><small>Conexión HTTPS · Cookie HttpOnly · Sesión temporal</small></div></section></div>;
}

function App(){
  const [auth,setAuth]=useState(undefined);
  const [period,setPeriod]=useState('7d');
  const [excludeInternal,setExcludeInternal]=useState(true);
  const [sampleSize,setSampleSize]=useState(5000);
  const [models,setModels]=useState(['topito','qwen-local']); const [model,setModel]=useState('topito');
  const [runtimeConfig,setRuntimeConfig]=useState(null);
  const [data,setData]=useState(); const [map,setMap]=useState();
  const [error,setError]=useState(''); const [logs,setLogs]=useState(false);
  const load=()=>{setData();setError('');const filter=`exclude_internal=${excludeInternal}&sample_size=${sampleSize}`;Promise.all([request(`/dashboard?period=${period}&${filter}`),request(`/map?period=${period}&${filter}`)]).then(([d,m])=>{setData(d);setMap(m)}).catch(e=>setError(e.message))};
  useEffect(()=>{request('/auth/session').then(setAuth).catch(()=>setAuth(null));const expired=()=>setAuth(null);window.addEventListener('auth-expired',expired);return()=>window.removeEventListener('auth-expired',expired)},[]);
  useEffect(()=>{if(auth)request('/config').then(config=>{setRuntimeConfig(config);setModels(config.llm.models);setModel(config.llm.defaultModel)}).catch(()=>{})},[auth]);
  useEffect(()=>{if(auth)load()},[period,excludeInternal,sampleSize,auth]);
  const logout=async()=>{try{await request('/auth/logout',{method:'POST'})}finally{setAuth(null);setData(undefined)}};
  if(auth===undefined)return <div className="auth-loading"><RefreshCw className="spin"/>Validando sesión segura…</div>;
  if(!auth)return <LoginScreen onLogin={setAuth}/>;
  return <main><nav><div className="brand"><div><ShieldCheck/></div><span>RANGER<strong>INTELLIGENCE</strong></span></div><div className="nav-actions"><span className="signed-user">{auth.username}</span><label className="internal-toggle"><input type="checkbox" checked={excludeInternal} onChange={e=>setExcludeInternal(e.target.checked)}/><span/>Excluir usuarios internos</label><select value={model} onChange={e=>setModel(e.target.value)} title="Modelo de AI Gateway">{models.map(item=><option key={item} value={item}>LLM: {item}</option>)}</select><select className="sample-select" value={sampleSize} onChange={e=>setSampleSize(Number(e.target.value))} title="Tamaño de la muestra"><option value="1000">Muestra: 1.000</option><option value="5000">Muestra: 5.000</option><option value="10000">Muestra: 10.000</option><option value="30000">Muestra: 30.000</option><option value="50000">Muestra: 50.000</option><option value="100000">Muestra: 100.000</option></select><select value={period} onChange={e=>setPeriod(e.target.value)}><option value="24h">Últimas 24 horas</option><option value="7d">Últimos 7 días</option><option value="30d">Últimos 30 días</option><option value="3m">Últimos 3 meses</option><option value="6m">Últimos 6 meses</option></select><button onClick={load} title="Actualizar"><RefreshCw size={17}/></button><button onClick={()=>setLogs(true)}><FileClock size={17}/> Ver log</button><button onClick={logout}>Salir</button></div></nav><header className="hero"><div className="hero-title"><div className="hero-logo-shell"><img src={rangerHero} alt="Agente del centro de operaciones Ranger"/></div><div><p>SECURITY OVERVIEW</p><h1>Centro de control</h1><span>Visibilidad unificada de accesos y políticas de Apache Ranger.</span></div></div><div className="status"><i/> Auditoría {runtimeConfig?.audit?.source || 'solr'} · {runtimeConfig?.audit?.server || 'base2.mole4.local'} · {excludeInternal?'Usuarios internos excluidos':'Todos los usuarios'}</div></header>{error?<div className="alert"><AlertTriangle/> {error}</div>:<Dashboard data={data} map={map}/>}<Chat period={period} excludeInternal={excludeInternal} sampleSize={sampleSize} model={model}/><footer>Muestra de {number(sampleSize)} auditorías · Consultas de solo lectura · {data?.configuredServices?.join(' · ')}</footer>{logs&&<Logs close={()=>setLogs(false)}/>}</main>;
}

createRoot(document.getElementById('root')).render(<App/>);
