import test from 'node:test';
import assert from 'node:assert/strict';
import {openDatabase} from '../src/db.mjs';
import {AuthService} from '../src/auth.mjs';
import {NotificationService} from '../src/service.mjs';
import {SmtpEmailProvider,buildMessage} from '../src/providers.mjs';
import {sendSmtpMail} from '../src/smtp.mjs';

test('注册邮箱关联自己的员工档案，修改不重复，启停与账号同步',()=>{
 const db=openDatabase(':memory:');const auth=new AuthService(db);
 const user=auth.registerUser({username:'worker01',password:'StrongPass-2026',display_name:'员工甲',email:'WORKER@example.com'});
 const contact=auth.contact(user.id);assert.equal(contact.email,'worker@example.com');assert.equal(contact.enabled,true);
 assert.equal(db.prepare('SELECT count(*) n FROM personnel').get().n,1);
 const service=new NotificationService(db);service.upsertPersonnel({id:contact.personnel_id,name:'员工甲',email:contact.email,phone:'+8613800000001'});
 auth.saveContact(user.id,{email:'changed@example.com'});
 assert.equal(auth.contact(user.id).personnel_id,contact.personnel_id);assert.equal(auth.contact(user.id).phone,'+8613800000001');
 assert.equal(db.prepare('SELECT count(*) n FROM personnel').get().n,1);
 auth.saveUser({username:'worker01',display_name:'员工甲',role:'viewer',enabled:false});assert.equal(auth.contact(user.id).enabled,false);
 assert.throws(()=>auth.saveContact(user.id,{email:'bad\r\nBcc:x@example.com'}),/有效/);
 assert.throws(()=>auth.saveContact(user.id,{email:'x@example.com',user_id:'other'}),/当前账号/);
 db.close();
});

test('注册邮箱无效时整笔回滚；已有无邮箱账户可补齐',()=>{
 const db=openDatabase(':memory:');const auth=new AuthService(db);
 assert.throws(()=>auth.registerUser({username:'baduser',password:'StrongPass-2026',email:'invalid'}),/有效/);
 assert.equal(auth.userCount(),0);
 const old=auth.saveUser({username:'olduser',password:'StrongPass-2026'});
 assert.equal(auth.contact(old.id).email,null);
 auth.saveContact(old.id,{email:'old@example.com'});assert.equal(auth.contact(old.id).email,'old@example.com');
 db.close();
});

test('日发电消息自动使用注册邮箱，新邮箱不重写历史收件人快照',()=>{
 const db=openDatabase(':memory:');const auth=new AuthService(db);const service=new NotificationService(db);
 const user=auth.registerUser({username:'worker02',password:'StrongPass-2026',email:'first@example.com'});
 service.upsertDailyGeneration({plant_id:'test',local_date:'2026-10-23',actual_kwh:3,forecast_kwh:4,business_timezone:'Asia/Shanghai'});
 const event=service.getEvent(service.listEvents({category:'daily'})[0].id);
 assert.equal(event.deliveries.find(d=>d.channel==='email').address_snapshot,'first@example.com');
 auth.saveContact(user.id,{email:'second@example.com'});
 assert.equal(service.getEvent(event.id).deliveries.find(d=>d.channel==='email').address_snapshot,'first@example.com');
 db.close();
});

test('真实 SMTP 测试白名单不会触达其他员工，演练邮件标题明确',async()=>{
 let sent=0;const provider=new SmtpEmailProvider({host:'smtp.qq.com',from:'test@qq.com',testRecipients:['test@qq.com']},async()=>{sent++;return {accepted:true,simulated:false};});
 await assert.rejects(()=>provider.send({address_snapshot:'other@example.com'}),/仅允许/);assert.equal(sent,0);
 await provider.send({address_snapshot:'test@qq.com',event_type:'model_anomaly',simulated:1,fact_snapshot:'{}'});assert.equal(sent,1);
 assert.match(buildMessage({event_type:'model_anomaly',simulated:1,fact_snapshot:'{}'}).subject,/演练/);
 await assert.rejects(()=>sendSmtpMail({host:'smtp.qq.com',secure:false},{}),/TLS/);
});

test('启用自动邮件时阻止历史任务和未关联账号，允许新的注册员工任务',async()=>{
 const {NotificationWorker}=await import('../src/worker.mjs');
 const db=openDatabase(':memory:'),auth=new AuthService(db),service=new NotificationService(db);
 const user=auth.registerUser({username:'notify01',password:'StrongPass-2026',email:'notify@example.com'});
 service.upsertPersonnel({id:'legacy',name:'未关联人员',email:'legacy@example.com'});
 service.upsertDailyGeneration({plant_id:'test',local_date:'2026-10-23',actual_kwh:3,forecast_kwh:4,business_timezone:'Asia/Shanghai'});
 let count=0;const providers={email:{name:'smtp',async send(){count++;return {accepted:true,simulated:false};}},sms:{name:'mock',async send(){return {accepted:true,simulated:true};}}};
 const worker=new NotificationWorker(db,providers,{registeredEmailOnly:true,emailStartAt:'9999-01-01T00:00:00.000Z',providerTimeoutMs:50});
 const rows=db.prepare(`SELECT d.*,e.type event_type,e.fact_snapshot,e.simulated FROM notification_delivery d JOIN notification_event e ON e.id=d.event_id WHERE d.channel='email'`).all();
 const claim=recipient=>{const id=rows.find(r=>r.recipient_id===recipient).id;db.prepare("UPDATE notification_delivery SET status='processing',lease_owner=? WHERE id=?").run(worker.workerId,id);return db.prepare('SELECT d.*,e.type event_type,e.fact_snapshot,e.simulated FROM notification_delivery d JOIN notification_event e ON e.id=d.event_id WHERE d.id=?').get(id);};
 await worker.processOne(claim(auth.contact(user.id).personnel_id));assert.equal(count,0);
 worker.emailStartAt='2000-01-01T00:00:00.000Z';
 await worker.processOne(claim('legacy'));assert.equal(count,0);
 await worker.processOne(claim(auth.contact(user.id).personnel_id));assert.equal(count,1);
 db.close();
});
