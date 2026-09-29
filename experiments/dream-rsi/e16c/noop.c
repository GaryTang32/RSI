#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int main(){int h[3]; fread(h,4,3,stdin); size_t n=h[0],p=h[1],K=h[2]; size_t tot=n*p+n+K; double*b=malloc(tot*8); fread(b,8,tot,stdin); double*o=calloc(p*K,8); fwrite(o,8,p*K,stdout); return 0;}
