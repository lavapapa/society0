import type {AgentMessage} from '@earendil-works/pi-agent-core';
import {isDeepStrictEqual} from 'node:util';
import type {RequiredAction,ActionReceiptReader} from '../contracts/inference.js';
export async function completedActions(required:readonly RequiredAction[],reader:ActionReceiptReader){const receipts=await reader.read();return {receipts,satisfied:required.every(rule=>new Set(receipts.filter(item=>item.action===rule.name&&item.receipt.status==='completed').map(item=>item.receipt.operationId)).size>=rule.minimumCompleted)}}
export function faithful(original:readonly AgentMessage[],candidate:readonly AgentMessage[]){let cursor=0;for(const message of candidate)if(cursor<original.length&&isDeepStrictEqual(original[cursor],message))cursor++;if(cursor!==original.length)throw Error('INFERENCE_HISTORY_FIDELITY')}
