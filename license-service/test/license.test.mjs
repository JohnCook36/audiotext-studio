import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto as crypto } from "node:crypto";
import worker from "../src/index.mjs";

class FakeD1 {
  constructor(){ this.rows=[]; }
  prepare(sql) {
    const db=this;
    let args=[];
    return {
      bind(...v){args=v;return this;},
      async run(){
        const s=sql.trim();
        if(s.startsWith("INSERT INTO licenses")){
          const [id,code_hash,label,term_days,created_at,updated_at]=args;
          if(db.rows.some(x=>x.code_hash===code_hash))throw Error("duplicate");
          db.rows.push({id,code_hash,label,term_days,created_at,updated_at,
             status:"active",device_hash:null,activated_at:null,expires_at:null});
          return {meta:{changes:1}};
        }
        if(s.includes("WHERE code_hash=?")){
          const [device,time,forExpiry,updated,hash,expected,now]=args;
          const row=db.rows.find(x=>x.code_hash===hash);
          if(!row||row.status!=="active"||(row.device_hash!==null&&row.device_hash!==expected)
             ||(row.expires_at!==null&&row.expires_at<=now))
            return {meta:{changes:0}};
          if(row.activated_at===null && row.term_days!==null)
            row.expires_at=forExpiry + row.term_days*86400;
          row.activated_at??=time;
          row.updated_at=updated;row.device_hash=device;
          return {meta:{changes:1}};
        }
        if(s.startsWith("UPDATE licenses SET status=")){
          const row=db.rows.find(x=>x.id===args[1]);
          if(!row)return {meta:{changes:0}};
          row.status="revoked";return {meta:{changes:1}};
        }
        if(s.startsWith("UPDATE licenses SET device_hash=NULL")){
          const row=db.rows.find(x=>x.id===args[1]&&x.status==="active");
          if(!row)return {meta:{changes:0}};
          row.device_hash=null;row.activated_at=null;row.expires_at=null;
          return {meta:{changes:1}};
        }
        throw Error("unknown SQL run: "+s);
      },
      async first(){
        if(sql.includes("WHERE code_hash=?"))return db.rows.find(x=>x.code_hash===args[0])??null;
        if(sql.includes("WHERE id=?"))return db.rows.find(x=>x.id===args[0])??null;
        throw Error("unknown SQL select");
      },
      async all(){
        return {results:db.rows.map(({id,label,status,term_days,activated_at,
                 expires_at,created_at,device_hash})=>({id,label,status,term_days,
                 activated_at,expires_at,created_at,activated:device_hash?1:0}))};
      }
    };
  }
}
const enc = data=>Buffer.from(data).toString("base64");
async function setup(){
  const keys=await crypto.subtle.generateKey("Ed25519",true,["sign","verify"]);
  return {
    DB:new FakeD1(),
    ATS_ADMIN_TOKEN:"long-random-admin-token-for-tests",
    ATS_SIGNING_PRIVATE_KEY:enc(await crypto.subtle.exportKey("pkcs8",keys.privateKey)),
    ATS_SIGNING_PUBLIC_KEY:enc(await crypto.subtle.exportKey("raw",keys.publicKey))
  };
}
const deviceA="a".repeat(64),deviceB="b".repeat(64);
const req=(path,body,admin=false)=>new Request("https://example.workers.dev"+path,{
  method:"POST",headers:{"content-type":"application/json",
    ...(admin?{"authorization":"Bearer long-random-admin-token-for-tests"}:{})},
  body:JSON.stringify(body)
});
const call=async (env,r)=>{const response=await worker.fetch(r,env);return {status:response.status,data:await response.json()};};

test("Health works before secrets configured",async()=>{
  const result=await call({},new Request("https://example.workers.dev/health"));
  assert.equal(result.status,200);
  assert.equal(result.data.ok,true);
});

test("Admin token required, first device wins, renewal respects revocation",async()=>{
  const env=await setup();
  let result=await call(env,req("/v1/admin/issue",{quantity:1}));
  assert.equal(result.status,401);
  result=await call(env,req("/v1/admin/issue",{quantity:1,term_days:30},true));
  assert.equal(result.status,200);
  const {code,id}=result.data.codes[0];
  assert.match(code,/^ATS-(?:[A-HJ-NP-Z2-9]{5}-){3}[A-HJ-NP-Z2-9]{5}$/);
  const activation=await call(env,req("/v1/activate",{code,device_hash:deviceA}));
  assert.equal(activation.status,200);
  const token=activation.data.token;
  assert.ok(token.includes("."));
  assert.equal((await call(env,req("/v1/activate",{code,device_hash:deviceB}))).status,403);
  const renewed=await call(env,req("/v1/renew",{token}));
  assert.equal(renewed.status,200);
  assert.equal((await call(env,req("/v1/admin/revoke",{id},true))).status,200);
  assert.equal((await call(env,req("/v1/renew",{token}))).status,403);
});

test("Reset frees license for another device, malformed codes rejected",async()=>{
  const env=await setup();
  const issued=(await call(env,req("/v1/admin/issue",{quantity:1},true))).data.codes[0];
  assert.equal((await call(env,req("/v1/activate",{code:"ATS-TEST",device_hash:deviceA}))).status,400);
  assert.equal((await call(env,req("/v1/activate",{code:issued.code,device_hash:deviceA}))).status,200);
  assert.equal((await call(env,req("/v1/admin/reset",{id:issued.id},true))).status,200);
  assert.equal((await call(env,req("/v1/activate",{code:issued.code,device_hash:deviceB}))).status,200);
  const list=await call(env,new Request("https://example.workers.dev/v1/admin/licenses",{
    headers:{"authorization":"Bearer long-random-admin-token-for-tests"}}));
  assert.equal(list.data.licenses.length,1);
  assert.equal(list.data.licenses[0].activated,1);
  assert.equal(JSON.stringify(list.data).includes(issued.code),false);
});
