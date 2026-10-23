/** One explicitly authorized self-addressed SMTP test. Does not drain employee outbox. */
import {readFileSync,existsSync,openSync,writeFileSync,closeSync,chmodSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {sendSmtpMail} from './smtp.mjs';
const [envPath,receiptPath]=process.argv.slice(2);
if(!envPath||!receiptPath)throw new Error('Usage: node src/test-smtp.mjs PRIVATE_ENV_PATH PRIVATE_RECEIPT_PATH');
const settings=Object.fromEntries(readFileSync(envPath,'utf8').split(/\r?\n/).filter(line=>line&& !line.startsWith('#')&&line.includes('=')).map(line=>{const i=line.indexOf('=');return [line.slice(0,i).trim(),line.slice(i+1).trim().replace(/^['"]|['"]$/g,'')];}));
const user=settings.FUMAI_SMTP_USER,to=settings.FUMAI_SMTP_TEST_TO;
if(!user||to!==user||settings.FUMAI_SMTP_FROM!==user)throw new Error('测试邮件必须为同一邮箱自收自发。');
if(!settings.FUMAI_SMTP_PASSWORD)throw new Error('请先在私密配置文件填写 SMTP 授权码。');
if(settings.FUMAI_SMTP_HOST!=='smtp.qq.com'||settings.FUMAI_SMTP_PORT!=='465')throw new Error('本次测试仅允许 QQ 邮箱的 smtp.qq.com:465 TLS。');
if(existsSync(receiptPath)) {const previous=JSON.parse(readFileSync(receiptPath,'utf8'));console.log(JSON.stringify({status:previous.status,message:'已有测试记录，未重复发送。接受状态未知时请先在邮箱核对。'}));process.exit(0);}
const descriptor=openSync(receiptPath,'wx',0o600);
const receipt={status:'sending',started_at:new Date().toISOString(),recipient_hash:createHash('sha256').update(to).digest('hex')};
writeFileSync(descriptor,JSON.stringify(receipt));closeSync(descriptor);
try {
 const result=await sendSmtpMail({host:'smtp.qq.com',port:465,secure:true,user,password:settings.FUMAI_SMTP_PASSWORD,from:user,helo:'fumai-zhiwei.local',timeoutMs:10000},
  {to,subject:'[伏脉智维] SMTP 自收自发测试',text:'这是一封你授权的伏脉智维邮件功能测试。发送方和接收方均为你自己的邮箱。本邮件不包含员工资料、设备告警或任何密码。若收到此邮件，说明服务器至 QQ 邮箱的 SMTP 发送链路可用；正式员工通知仍需单独启用。',html:'<p>这是一封你授权的伏脉智维邮件功能测试。</p><p>本邮件不包含员工资料、设备告警或任何密码。正式员工通知仍需单独启用。</p>'});
 Object.assign(receipt,{status:'accepted',finished_at:new Date().toISOString(),provider_message_id:result.providerMessageId});
 writeFileSync(receiptPath,JSON.stringify(receipt),{mode:0o600});chmodSync(receiptPath,0o600);
 console.log(JSON.stringify({status:'accepted',message:'QQ SMTP 已接受这一封自收自发测试邮件，请在邮箱确认收到。',message_id:result.providerMessageId}));
} catch(error){
 Object.assign(receipt,{status:error.unknownAcceptance?'verify_pending':'failed',finished_at:new Date().toISOString(),error_code:error.code||'SMTP_SEND_FAILED'});
 writeFileSync(receiptPath,JSON.stringify(receipt),{mode:0o600});
 console.error(JSON.stringify({status:receipt.status,error_code:receipt.error_code,message:'测试未确认成功，请按错误码核对配置；未发送任何员工通知。'}));process.exitCode=1;
}
