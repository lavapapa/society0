import {openConnection,createInterruptControl,type SqliteRow,type ConnectionState} from '@society0/sqlite-native';
const db=openConnection({path:':memory:',control:createInterruptControl()});
const rows:SqliteRow[]=db.query('SELECT ? AS v',[1n],{maxRows:1,maxBytes:64});
const state:ConnectionState=db.state();
void rows;void state;
db.close();
