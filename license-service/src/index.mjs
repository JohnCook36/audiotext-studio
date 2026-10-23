// Cloudflare Workers + D1 activation service for AudioText Studio.
const enc = new TextEncoder();
const now = () => Math.floor(Date.now()/1000);
const ABC="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const response = (o,status=200) => new Response(JSON.stringify(o),{status,headers:{
  "content-type":"application/json; charset=utf-8","cache-control":"no-store","x-content-type-options":"nosniff"
}});
const reject = (message,status=400) => response({ok:false,error:message},status);
const decode = x => Uint8Array.from(atob(x.replace(/-/g,"+").replace(/_/g,"/")),c=>c.charCodeAt(0));
const encode = x => btoa(String.fromCharCode(...x)).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
const codeClean = x => String(x||"").toUpperCase().replace(/[\s-]/g,"");
const validDevice = x => typeof x==="string" && /^[a-f0-9]{64}$/.test(x);
async function sha(x){
  const b=await crypto.subtle.digest("SHA-256",enc.encode(x));
  return Array.from(new Uint8Array(b),n=>n.toString(16).padStart(2,"0")).join("");
}
async function readBody(r){
  if(Number(r.headers.get("content-length")||0)>4096) throw Error("Body too large");
  const text=await r.text();
  if(text.length>4096) throw Error("Body too large");
  return JSON.parse(text);
}
const tokenExpiry=row=>Math.min(now()+90*86400,row.expires_at??Number.MAX_SAFE_INTEGER);
async function sign(row,device,env){
  const claims={v:1,iss:"AudioTextStudio",license_id:row.id,device_hash:device,
    issued_at:now(),expires_at:tokenExpiry(row)};
  if(claims.expires_at<=claims.issued_at) return null;
  const payload=encode(enc.encode(JSON.stringify(claims)));
  const key=await crypto.subtle.importKey("pkcs8",decode(env.ATS_SIGNING_PRIVATE_KEY),"Ed25519",false,["sign"]);
  const signature=await crypto.subtle.sign("Ed25519",key,enc.encode(payload));
  return payload+"."+encode(new Uint8Array(signature));
}
async function verify(token,env){
  if(typeof token!=="string"||token.length>4096) return null;
  const p=token.split(".");
  if(p.length!==2) return null;
  try{
    const key=await crypto.subtle.importKey("raw",decode(env.ATS_SIGNING_PUBLIC_KEY),"Ed25519",false,["verify"]);
    if(!await crypto.subtle.verify("Ed25519",key,decode(p[1]),enc.encode(p[0])))return null;
    const x=JSON.parse(new TextDecoder().decode(decode(p[0])));
    return x.v===1&&x.iss==="AudioTextStudio"&&validDevice(x.device_hash)?x:null;
  }catch{return null;}
}
function adminAuth(request,env){
  const actual=request.headers.get("authorization")||"";
  const expected="Bearer "+env.ATS_ADMIN_TOKEN;
  if(!env.ATS_ADMIN_TOKEN)return false;
  let mismatch=actual.length^expected.length;
  for(let i=0;i<Math.max(actual.length,expected.length);i++)
    mismatch|=(actual.charCodeAt(i)||0)^(expected.charCodeAt(i)||0);
  return mismatch===0;
}
async function activate(request,env){
  const data=await readBody(request);
  const code=codeClean(data.code),device=data.device_hash;
  if(!/^ATS[A-HJ-NP-Z2-9]{20}$/.test(code)||!validDevice(device))
    return reject("Неверный формат кода или идентификатора устройства");
  const hash=await sha(code),time=now();
  // Atomic claim: only the first hardware ID can take an unused activation.
  await env.DB.prepare(
    "UPDATE licenses SET device_hash=?,activated_at=COALESCE(activated_at,?),"+
    "expires_at=CASE WHEN activated_at IS NULL AND term_days IS NOT NULL "+
    "THEN ?+term_days*86400 ELSE expires_at END,updated_at=? "+
    "WHERE code_hash=? AND status='active' AND (device_hash IS NULL OR device_hash=?) "+
    "AND (expires_at IS NULL OR expires_at>?)"
  ).bind(device,time,time,time,hash,device,time).run();
  const row=await env.DB.prepare(
    "SELECT id,device_hash,status,expires_at FROM licenses WHERE code_hash=?"
  ).bind(hash).first();
  if(!row||row.status!=="active"||row.device_hash!==device||
     (row.expires_at!=null&&row.expires_at<=time))
    return reject("Код недействителен или уже активирован на другом компьютере",403);
  return response({ok:true,token:await sign(row,device,env)});
}
async function refresh(request,env){
  const data=await readBody(request),claim=await verify(data.token,env);
  if(!claim)return reject("Неверная подпись лицензии",403);
  const row=await env.DB.prepare(
    "SELECT id,device_hash,status,expires_at FROM licenses WHERE id=?"
  ).bind(claim.license_id).first();
  if(!row||row.status!=="active"||row.device_hash!==claim.device_hash||
     (row.expires_at!=null&&row.expires_at<=now()))
    return reject("Лицензия отозвана или истекла",403);
  return response({ok:true,token:await sign(row,claim.device_hash,env)});
}
function newCode(){
  const bytes=crypto.getRandomValues(new Uint8Array(20));
  const v=Array.from(bytes,x=>ABC[x%ABC.length]).join("");
  return "ATS-"+(v.match(/.{5}/g)||[]).join("-");
}
async function issue(request,env){
  const x=await readBody(request),amount=Number(x.quantity??1);
  const term=x.term_days==null?null:Number(x.term_days);
  if(!Number.isInteger(amount)||amount<1||amount>50)return reject("Количество: от 1 до 50");
  if(term!=null&&(!Number.isInteger(term)||term<1||term>3650))return reject("Срок: 1–3650 дней или null");
  const label=String(x.label||"").slice(0,100),result=[];
  for(let i=0;i<amount;i++){
    const id=crypto.randomUUID(),code=newCode(),time=now();
    await env.DB.prepare(
      "INSERT INTO licenses(id,code_hash,label,term_days,status,device_hash,"+
      "activated_at,expires_at,created_at,updated_at) VALUES(?,?,?,?,'active',NULL,NULL,NULL,?,?)"
    ).bind(id,await sha(codeClean(code)),label,term,time,time).run();
    result.push({id,code});
  }
  // Codes are returned only once and never persisted in plaintext.
  return response({ok:true,codes:result});
}
async function adminList(env){
  const x=await env.DB.prepare(
    "SELECT id,label,status,term_days,activated_at,expires_at,created_at,"+
    "CASE WHEN device_hash IS NULL THEN 0 ELSE 1 END AS activated "+
    "FROM licenses ORDER BY created_at DESC LIMIT 200"
  ).all();
  return response({ok:true,licenses:x.results});
}
async function modify(request,env,action){
  const x=await readBody(request);
  if(typeof x.id!=="string"||!/^[0-9a-f-]{36}$/.test(x.id))return reject("Неверный ID");
  const sql=action==="revoke"
    ?"UPDATE licenses SET status='revoked',updated_at=? WHERE id=?"
    :"UPDATE licenses SET device_hash=NULL,activated_at=NULL,expires_at=NULL,updated_at=? WHERE id=? AND status='active'";
  const result=await env.DB.prepare(sql).bind(now(),x.id).run();
  return result.meta.changes?response({ok:true}):reject("Лицензия не найдена",404);
}
export default {
  async fetch(request,env){
    const path=new URL(request.url).pathname;
    if(request.method==="GET"&&path==="/health")
      return response({ok:true,service:"audiotext-studio-licenses"});
    if(!env.DB||!env.ATS_SIGNING_PRIVATE_KEY||!env.ATS_SIGNING_PUBLIC_KEY)
      return reject("Сервис ещё не настроен",503);
    try{
      if(request.method==="POST"&&path==="/v1/activate")return await activate(request,env);
      if(request.method==="POST"&&path==="/v1/renew")return await refresh(request,env);
      if(path.startsWith("/v1/admin/")){
        if(!adminAuth(request,env))return reject("Доступ запрещён",401);
        if(request.method==="POST"&&path==="/v1/admin/issue")return await issue(request,env);
        if(request.method==="GET"&&path==="/v1/admin/licenses")return await adminList(env);
        if(request.method==="POST"&&path==="/v1/admin/revoke")return await modify(request,env,"revoke");
        if(request.method==="POST"&&path==="/v1/admin/reset")return await modify(request,env,"reset");
      }
      return reject("Не найдено",404);
    }catch(err){
      if(err instanceof SyntaxError)return reject("Некорректный JSON",400);
      if(err.message==="Body too large")return reject("Слишком большой запрос",413);
      console.error("License API error:",err.name);
      return reject("Внутренняя ошибка",500);
    }
  }
};
