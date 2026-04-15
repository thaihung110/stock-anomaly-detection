# Xóa resource cũ
kubectl delete deployment ingress-nginx-controller -n ingress-nginx --ignore-not-found
kubectl delete svc ingress-nginx-controller -n ingress-nginx --ignore-not-found
kubectl delete svc ingress-nginx-controller-admission -n ingress-nginx --ignore-not-found
kubectl delete ingressclass nginx --ignore-not-found
kubectl delete namespace ingress-nginx --ignore-not-found


kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.13.0/deploy/static/provider/cloud/deploy.yaml

sudo --preserve-env=HOME minikube tunnel
