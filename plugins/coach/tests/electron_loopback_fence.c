/* Test-only loopback fence inherited by subprocesses. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
static int allowed(const struct sockaddr *a) {
 if (!a) return 1;
 if (a->sa_family == AF_INET) return (ntohl(((const struct sockaddr_in*)a)->sin_addr.s_addr) >> 24) == 127;
 if (a->sa_family == AF_INET6) return IN6_IS_ADDR_LOOPBACK(&((const struct sockaddr_in6*)a)->sin6_addr);
 return a->sa_family == AF_UNIX || a->sa_family == AF_UNSPEC || a->sa_family == AF_NETLINK;
}
int connect(int fd,const struct sockaddr *a,socklen_t n) {
 if (!allowed(a)) {errno=EACCES;return -1;}
 return ((int (*)(int,const struct sockaddr*,socklen_t))dlsym(RTLD_NEXT,"connect"))(fd,a,n);
}
ssize_t sendto(int fd,const void *b,size_t n,int flags,const struct sockaddr *a,socklen_t alen) {
 if (!allowed(a)) {errno=EACCES;return -1;}
 return ((ssize_t (*)(int,const void*,size_t,int,const struct sockaddr*,socklen_t))dlsym(RTLD_NEXT,"sendto"))(fd,b,n,flags,a,alen);
}

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
int kill(pid_t pid,int sig) {
 if (sig != 0) {
  char file[64],buf[262144]; const char *home=getenv("HERMES_HOME"); int found=0;
  if (pid>0 && home && *home) {
   snprintf(file,sizeof(file),"/proc/%d/environ",pid); FILE *f=fopen(file,"rb");
   if(f) {size_t n=fread(buf,1,sizeof(buf)-1,f);fclose(f);buf[n]=0;
    for(size_t i=0;i<n;) {size_t len=strlen(buf+i); if(strncmp(buf+i,"HERMES_HOME=",12)==0 && strcmp(buf+i+12,home)==0) found=1; i+=len+1;}
   }
  }
  if(!found && pid!=getpid()) {errno=EPERM;return -1;}
 }
 return ((int (*)(pid_t,int))dlsym(RTLD_NEXT,"kill"))(pid,sig);
}
